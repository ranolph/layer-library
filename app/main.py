"""Trusted-LAN model library with browser CSRF protection and printer transfers."""
from __future__ import annotations

import hashlib
import hmac
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, StrictBool
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from .mesh import ModelError, inspect_3mf, inspect_job, inspect_stl
from .obj_mesh import inspect_obj
from .thumbnails import render_thumbnail

VERSION = "1.0.6"
STATIC = Path(__file__).parent / "static"
COOKIE = "layer_library_session"
SESSION_SECONDS = 12 * 60 * 60
log = logging.getLogger("layer_library")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_filename(name):
    name = str(name or "model.stl").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if ord(c) >= 32 and ord(c) != 127).strip(" .")
    if len(name) > 180:
        suffix = ".gcode.3mf" if name.lower().endswith(".gcode.3mf") else Path(name).suffix[:12]
        name = name[:180 - len(suffix)] + suffix
    return name or "model.stl"


def clean_text(value, limit):
    return "".join(c for c in str(value or "") if c in "\n\t" or ord(c) >= 32).strip()[:limit]


def validate_host(host):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as e:
        raise ValueError("Enter the printer's private LAN IP address, without http:// or a port.") from e
    networks = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")]
    if not any(ip.version == n.version and ip in n for n in networks):
        raise ValueError("Use a private LAN printer address (10.x, 172.16–31.x, 192.168.x, or IPv6 ULA).")
    return str(ip)


def validate_backup_origin(value):
    """Accept exactly one HTTP(S) origin for the shared backup service."""
    try:
        if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value:
            raise ValueError()
        parts = urlsplit(value)
        if (parts.scheme not in ("http", "https") or not parts.hostname or
                parts.username is not None or parts.password is not None or
                parts.path or parts.query or parts.fragment):
            raise ValueError()
        port = parts.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        host = parts.hostname.lower()
        if ":" in host:
            if "%" in host:
                raise ValueError()
            host = "[" + str(ipaddress.IPv6Address(host)) + "]"
        else:
            host = host.encode("idna").decode("ascii")
            if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
                                         for part in host.split(".")):
                raise ValueError()
        return parts.scheme + "://" + host + (":" + str(port) if port is not None else "")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("BACKUP_SERVICE_ORIGIN must be one HTTP(S) origin without credentials, path, query or wildcard.") from exc


class Store:
    def __init__(self, data, seed, password=None):
        # The optional legacy argument is accepted for upgrade compatibility;
        # trusted-LAN access does not read or create a workspace password.
        self.data, self.seed = Path(data), Path(seed) if seed else None
        self.files = self.data / "files"
        self.thumbs = self.data / "thumbnails"
        self.temp = self.data / "incoming"
        self.studio = self.data / "studio_cache"
        self.dbfile = self.data / "library.sqlite3"
        self.max_upload = int(os.environ.get("MAX_UPLOAD_MB", "250")) * 1024**2
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="printer-transfer")
        self.lock = threading.Lock()
        # Only one mesh inspection holds geometry buffers at a time, including
        # simultaneous uploads from different browser sessions.
        self.inspection_lock = threading.Lock()
        # Failed thumbnails get a short cooldown, so a corrupt model cannot
        # trigger another expensive render on every library/card refresh.
        self.thumbnail_failures = {}
        self.thumbnail_state_lock = threading.Lock()

    @staticmethod
    def usable_thumbnail(path):
        """Detect old blank canvas captures and incomplete image writes."""
        try:
            with Image.open(path) as image:
                if image.format != "PNG" or image.width < 2 or image.height < 2 or image.width * image.height > 4_000_000:
                    return False
                image.load()
                # A successful model view has some contrast. Older hidden or
                # zero-sized WebGL canvases sometimes produced a flat colour.
                extrema = image.convert("RGB").getextrema()
                return any(high - low > 8 for low, high in extrema)
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            return False

    def ensure_thumbnail(self, model_id, force=False):
        row = self.row(model_id, include_deleted=not force)
        destination = self.thumbs / f"{model_id}.png"
        if not force and self.usable_thumbnail(destination):
            return destination
        with self.thumbnail_state_lock:
            previous = self.thumbnail_failures.get(model_id)
        if not force and previous and time.monotonic() - previous[0] < 60:
            raise ModelError(previous[1])
        if not self.inspection_lock.acquire(timeout=65):
            raise ModelError("Another model is still being processed. Try Rebuild preview image in a moment.")
        try:
            # Requests from several cards/tabs can arrive together. Recheck
            # after acquiring the single geometry-processing slot.
            if not force and self.usable_thumbnail(destination):
                return destination
            with self.thumbnail_state_lock:
                previous = self.thumbnail_failures.get(model_id)
            if not force and previous and time.monotonic() - previous[0] < 60:
                raise ModelError(previous[1])
            try:
                render_thumbnail(self.model_path(row), row["kind"], destination)
            except (ModelError, OSError, ValueError) as exc:
                message = str(exc)[:400] if isinstance(exc, ModelError) else "The preview image could not be created. The original model is still available to download."
                with self.thumbnail_state_lock:
                    if len(self.thumbnail_failures) >= 512:
                        self.thumbnail_failures.pop(next(iter(self.thumbnail_failures)), None)
                    self.thumbnail_failures[model_id] = (time.monotonic(), message)
                log.warning("Thumbnail generation failed for model %s (%s)", model_id, type(exc).__name__)
                raise ModelError(message) from exc
            with self.thumbnail_state_lock:
                self.thumbnail_failures.pop(model_id, None)
            return destination
        finally:
            self.inspection_lock.release()

    def save_browser_thumbnail(self, mid, data):
        """Keep canvas decoding and the geometry lock off the ASGI event loop."""
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "PNG" or im.width * im.height > 4_000_000:
                raise ValueError()
            im.thumbnail((800, 600))
            image = im.convert("RGB")
            if image.width < 2 or image.height < 2 or not any(high - low > 8 for low, high in image.getextrema()):
                raise ValueError()
            destination = self.thumbs / f"{mid}.png"
            # A delayed browser capture must not replace a completed server
            # image. Explicit Rebuild is the route for replacing an image.
            with self.inspection_lock:
                if not self.usable_thumbnail(destination):
                    temporary = self.thumbs / f"{mid}-{uuid.uuid4().hex}.tmp"
                    try:
                        image.save(temporary, "PNG")
                        os.replace(temporary, destination)
                    finally:
                        temporary.unlink(missing_ok=True)
                    with self.thumbnail_state_lock:
                        self.thumbnail_failures.pop(mid, None)

    @contextmanager
    def db(self):
        con = sqlite3.connect(self.dbfile, timeout=20)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def setting(self, key, default=None):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def initialize(self):
        for path in (self.data, self.files, self.thumbs, self.temp, self.studio):
            path.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,csrf TEXT NOT NULL,expires INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS models(
                id TEXT PRIMARY KEY,title TEXT NOT NULL,filename TEXT NOT NULL,kind TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,sha256 TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,dimensions TEXT NOT NULL,triangle_count INTEGER NOT NULL,
                tags TEXT NOT NULL DEFAULT '[]',collection TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',
                deleted_at TEXT,featured INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY,model_id TEXT NOT NULL,filename TEXT NOT NULL,size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,metadata TEXT NOT NULL,deleted_at TEXT);
              CREATE TABLE IF NOT EXISTS transfers(
                id TEXT PRIMARY KEY,job_id TEXT NOT NULL,status TEXT NOT NULL,progress REAL NOT NULL,
                message TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS jobs_model ON jobs(model_id);
            """)
            # The flag is shared library metadata, independent of collections.
            # Serialize the schema check and ALTER for simultaneous startups.
            db.execute("BEGIN IMMEDIATE")
            if "featured" not in {column["name"] for column in db.execute("PRAGMA table_info(models)")}:
                db.execute("ALTER TABLE models ADD COLUMN featured INTEGER NOT NULL DEFAULT 0")
            # Existing settings, including old password hashes, remain intact.
            # Sessions now identify browsers solely for CSRF protection.
            db.execute("UPDATE transfers SET status='interrupted',message='The server restarted before this transfer finished. Check printer storage before sending again.' WHERE status IN ('queued','sending')")
            db.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))
        # A failed seed import can be resumed; the SHA-256 constraint prevents duplicates.
        if self.seed and (self.seed / "manifest.json").is_file() and not self.setting("seed_imported"):
            manifest = json.loads((self.seed / "manifest.json").read_text())
            for entry in manifest["models"]:
                filename = safe_filename(entry["filename"])
                src = self.seed / filename
                temp = self.temp / f"{uuid.uuid4().hex}.part"
                shutil.copyfile(src, temp)
                item = self.add_model(temp, filename, entry.get("collection", ""), entry)
                thumb = self.seed / f"{Path(filename).stem}.png"
                if thumb.is_file():
                    shutil.copyfile(thumb, self.thumbs / f"{item['id']}.png")
            with self.db() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES('seed_imported','1')")

    def model_path(self, row):
        return self.files / f"{row['id']}.{row['kind']}"

    @staticmethod
    def studio_filename(row):
        # Studio inspects the URL path suffix before making its HTTP request.
        # Keep this path ASCII and query-free: Studio decodes the complete
        # protocol URL before parsing it, including any embedded delimiters.
        name = row["filename"]
        sliced = name.lower().endswith(".gcode.3mf")
        stem = name[:-len(".gcode.3mf")] if sliced else Path(name).stem
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-")[:120]
        return (stem or "model") + (".gcode.3mf" if sliced else ".3mf")

    def studio_path(self, row):
        if row["kind"] == "3mf":
            return self.model_path(row)
        return self.studio / f"{row['id']}-v1.3mf"

    def prepare_studio(self, model_id):
        row = self.row(model_id)
        destination = self.studio_path(row)
        if row["kind"] != "3mf" and not destination.is_file():
            # Share the geometry slot with inspection and preview rendering.
            # A second request can retry instead of running several large
            # conversions at once or blocking the ASGI event loop.
            if not self.inspection_lock.acquire(timeout=5):
                raise HTTPException(409, "Another model is being processed. Try Open in Bambu Studio again in a moment.")
            try:
                if not destination.is_file():
                    from .studio_export import export_model_3mf
                    export_model_3mf(self.model_path(row), row["kind"], destination)
            finally:
                self.inspection_lock.release()
        # A model moved to Trash during preparation must not be offered.
        self.row(model_id)
        filename = self.studio_filename(row)
        return {"download_url": f"/api/models/{model_id}/studio/{filename}",
                "filename": filename, "converted": row["kind"] != "3mf",
                "size_bytes": destination.stat().st_size,
                "message": "Open this file in Bambu Studio, select your P2S and review its settings before slicing or sending."}

    def row(self, model_id, include_deleted=False):
        with self.db() as db:
            row = db.execute("SELECT * FROM models WHERE id=?" + ("" if include_deleted else " AND deleted_at IS NULL"), (model_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Model not found.")
        return row

    def model_json(self, row, count=None):
        dims = json.loads(row["dimensions"])
        if count is None:
            with self.db() as db:
                count = db.execute("SELECT COUNT(*) FROM jobs WHERE model_id=? AND deleted_at IS NULL", (row["id"],)).fetchone()[0]
        mid = row["id"]
        return {k: row[k] for k in ("id", "title", "filename", "kind", "size_bytes", "created_at", "updated_at", "collection", "notes", "triangle_count")} | {
            "dimensions": dims, "tags": json.loads(row["tags"]), "job_count": count,
            "featured": bool(row["featured"]),
            "thumbnail_url": f"/api/models/{mid}/thumbnail",
            "thumbnail_ready": (self.thumbs / f"{mid}.png").is_file(),
            "download_url": f"/api/models/{mid}/file", "preview_url": f"/api/models/{mid}/file",
            "fit_p2s": all(v <= 256.0001 for v in dims.values()), "deleted_at": row["deleted_at"]}

    def add_model(self, temp, filename, collection="", metadata=None):
        try:
            ext = Path(filename).suffix.lower()
            if ext not in (".stl", ".3mf", ".obj") or filename.lower().endswith(".gcode.3mf"):
                raise ModelError("Upload an STL, OBJ or model 3MF here. Add sliced .gcode.3mf files in a model's Print jobs tab.")
            with temp.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            with self.db() as db:
                previous = db.execute("SELECT * FROM models WHERE sha256=?", (digest,)).fetchone()
            if previous:
                if previous["deleted_at"]:
                    raise ModelError("This exact model is in Trash. Restore it there to use it again.")
                return self.model_json(previous)
            with self.inspection_lock:
                inspector = {".stl": inspect_stl, ".3mf": inspect_3mf, ".obj": inspect_obj}[ext]
                details = inspector(temp)
            mid, stamp = uuid.uuid4().hex, now()
            meta = metadata or {}
            title = clean_text(meta.get("title", Path(filename).stem.replace("_", " ")), 160)
            size = temp.stat().st_size
            dest = self.files / f"{mid}{ext}"
            os.replace(temp, dest)
            try:
                with self.db() as db:
                    db.execute("INSERT INTO models(id,title,filename,kind,size_bytes,sha256,created_at,updated_at,dimensions,triangle_count,tags,collection,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (mid, title, filename, ext[1:], size, digest, stamp, stamp,
                                json.dumps(details["dimensions"]), details["triangle_count"], json.dumps(meta.get("tags", [])),
                                clean_text(collection, 80), clean_text(meta.get("notes", ""), 10000)))
            except sqlite3.IntegrityError:
                dest.unlink(missing_ok=True)
                # A second simultaneous request may have imported the same
                # bytes while this request was inspecting the mesh.
                with self.db() as db:
                    existing = db.execute("SELECT * FROM models WHERE sha256=?", (digest,)).fetchone()
                if existing and not existing["deleted_at"]:
                    return self.model_json(existing)
                if existing:
                    raise ModelError("This exact model is in Trash. Restore it there to use it again.")
                raise
            except Exception:
                dest.unlink(missing_ok=True)
                raise
            return self.model_json(self.row(mid))
        finally:
            temp.unlink(missing_ok=True)

    def printer_settings(self):
        path = self.data / "printer.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text())

    def save_printer(self, config):
        path = self.data / "printer.json"
        temporary = self.data / f"printer-{uuid.uuid4().hex}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(config, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def public_printer(self):
        c = self.printer_settings()
        # Old credentials remain saved, but an upgrade never silently opts
        # an existing cloud workflow into an unsupported local transport.
        mode = "lan" if c.get("mode") == "lan" else "cloud"
        lan_configured = bool(c.get("host") and c.get("access_code"))
        lan_verified = bool(lan_configured and c.get("fingerprint"))
        return {"mode": mode, "configured": mode == "lan" and lan_configured,
                "name": c.get("name", "Bambu P2S"),
                "host": c.get("host", ""), "serial": c.get("serial", ""),
                "access_code_saved": bool(c.get("access_code")), "verified": mode == "lan" and lan_verified,
                "connection_verified": mode == "lan" and lan_verified,
                "lan_configured": lan_configured, "lan_verified": lan_verified,
                "certificate_fingerprint": c.get("fingerprint", "")}

    def job_row(self, jid):
        with self.db() as db:
            row = db.execute("SELECT j.* FROM jobs j JOIN models m ON m.id=j.model_id WHERE j.id=? AND j.deleted_at IS NULL AND m.deleted_at IS NULL", (jid,)).fetchone()
        if not row:
            raise HTTPException(404, "Print job not found.")
        return row

    def job_json(self, row):
        return {k: row[k] for k in ("id", "model_id", "filename", "size_bytes", "created_at")} | json.loads(row["metadata"])

    def update_transfer(self, tid, status, progress, message):
        with self.db() as db:
            db.execute("UPDATE transfers SET status=?,progress=?,message=?,updated_at=? WHERE id=?",
                       (status, progress, message, now(), tid))

    def transfer(self, tid, job, config):
        from .printer import upload_file
        try:
            self.update_transfer(tid, "sending", 0, "Connecting to printer storage…")
            last = [0.]
            def progress(sent, total):
                t = time.monotonic()
                if t - last[0] > .5 or sent == total:
                    self.update_transfer(tid, "sending", min(99., sent / max(total, 1) * 100), "Sending to printer storage…")
                    last[0] = t
            result = upload_file(config["host"], config["access_code"], self.files / f"{job['id']}.gcode.3mf",
                                 job["filename"], config["fingerprint"], progress)
            remote = result.get("remote_name", result.get("remote_path", job["filename"]))
            self.update_transfer(tid, "complete", 100, f"Stored on printer as {remote}. Start the job from the printer screen after checking material and plate settings.")
        except Exception as e:
            # Credentials must never appear in a status response or server log.
            message = str(e).replace(config.get("access_code", "\0"), "[redacted]")[:500]
            self.update_transfer(tid, "failed", 0, "Transfer failed: " + message + " Check printer storage before retrying.")


class EditModel(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    collection: str = Field(default="", max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=30)
    notes: str = Field(default="", max_length=10000)


class FeaturedModel(BaseModel):
    model_config = {"extra": "forbid"}
    featured: StrictBool


class PrinterConfig(BaseModel):
    mode: Literal["cloud", "lan"] = "cloud"
    name: str = Field(default="Bambu P2S", max_length=80)
    host: str = Field(default="", max_length=100)
    serial: str = Field(default="", max_length=64)
    access_code: str = Field(default="", max_length=128)


class SendJob(BaseModel):
    start_print: bool = False


class TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, received = scope.get("path", ""), 0
        limit = self.max_bytes if path.endswith("/upload") or path.endswith("/jobs") else 9 * 1024**2
        headers = dict(scope["headers"])
        if int(headers.get(b"content-length", b"0")) > limit:
            return await JSONResponse({"detail": "Upload exceeds the request size limit."}, 413)(scope, receive, send)
        started = False
        async def limited_receive():
            nonlocal received
            event = await receive()
            received += len(event.get("body", b""))
            if received > limit:
                raise TooLarge()
            return event
        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)
        try:
            await self.app(scope, limited_receive, tracked_send)
        except TooLarge:
            if not started:
                await JSONResponse({"detail": "Upload exceeds the request size limit."}, 413)(scope, receive, send)


def create_app(data_dir=None, seed_dir=None, initial_password=None):
    # initial_password remains an ignored compatibility argument for older
    # launchers. Fresh installations start without any password configuration.
    configured_backup = os.environ.get("BACKUP_SERVICE_ORIGIN", "")
    backup_origin = validate_backup_origin(configured_backup) if configured_backup else ""
    store = Store(data_dir or os.environ.get("DATA_DIR", "./data"),
                  seed_dir if seed_dir is not None else os.environ.get("SEED_DIR", ""))
    connect_sources = "'self'" + (" " + backup_origin if backup_origin else "")

    @asynccontextmanager
    async def lifespan(application):
        await run_in_threadpool(store.initialize)
        yield
        store.pool.shutdown(wait=True)

    app = FastAPI(title="Layer Library", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.add_middleware(BodyLimitMiddleware, max_bytes=store.max_upload * 10 + 2 * 1024**2)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = f"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src {connect_sources}; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def origin_check(request):
        if request.headers.get("sec-fetch-site", "").lower() not in ("", "same-origin", "none"):
            raise HTTPException(403, "Cross-site request rejected.")
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
                if (parsed.scheme.lower() != request.url.scheme.lower() or
                        parsed.netloc.lower() != request.headers.get("host", "").lower() or
                        parsed.path or parsed.query or parsed.fragment):
                    raise ValueError()
            except ValueError as exc:
                raise HTTPException(403, "Cross-site request rejected.") from exc

    def current_session(request):
        token = request.cookies.get(COOKIE, "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with store.db() as db:
            return db.execute("SELECT * FROM sessions WHERE token=? AND expires>?",
                              (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()

    def browser_access(request: Request):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        origin_check(request)
        session = current_session(request)
        if not session:
            raise HTTPException(403, "Refresh the browser session, then try again.")
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied.isascii() or not hmac.compare_digest(supplied, session["csrf"]):
            raise HTTPException(403, "Session check failed. Refresh the browser session and try again.")
        return session

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": VERSION}

    @app.get("/api/session")
    @app.get("/api/auth/status")
    def browser_session(request: Request, response: Response):
        # Automatically create or renew a browser session for CSRF protection.
        # No account, password or saved password hash is checked.
        origin_check(request)
        session = current_session(request)
        token = request.cookies[COOKIE] if session else secrets.token_urlsafe(32)
        csrf = session["csrf"] if session else secrets.token_urlsafe(32)
        stamp = int(time.time())
        with store.db() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (stamp,))
            db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), csrf, stamp + SESSION_SECONDS))
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=SESSION_SECONDS)
        # Legacy fields let a cached pre-upgrade frontend enter automatically.
        return {"authenticated": True, "setup_required": False, "login_required": False,
                "access_mode": "trusted_lan", "csrf_token": csrf,
                "backup_service_origin": backup_origin,
                "app_name": "Layer Library", "version": VERSION}

    @app.get("/api/library", dependencies=[Depends(browser_access)])
    def library():
        with store.db() as db:
            rows = db.execute("SELECT * FROM models WHERE deleted_at IS NULL ORDER BY created_at DESC,title").fetchall()
            counts = dict(db.execute("SELECT model_id,COUNT(*) FROM jobs WHERE deleted_at IS NULL GROUP BY model_id").fetchall())
            jobs, job_bytes = db.execute("SELECT COUNT(*),COALESCE(SUM(j.size_bytes),0) FROM jobs j JOIN models m ON m.id=j.model_id WHERE j.deleted_at IS NULL AND m.deleted_at IS NULL").fetchone()
        items = [store.model_json(r, counts.get(r["id"], 0)) for r in rows]
        collections = Counter(x["collection"] for x in items if x["collection"])
        tags = Counter(t for x in items for t in x["tags"])
        return {"items": items, "collections": [{"name": n, "count": c} for n, c in sorted(collections.items())],
                "tags": [{"name": n, "count": c} for n, c in sorted(tags.items())],
                "stats": {"models": len(items), "featured_count": sum(x["featured"] for x in items),
                          "stl_count": sum(x["kind"] == "stl" for x in items),
                          "three_mf_count": sum(x["kind"] == "3mf" for x in items),
                          "obj_count": sum(x["kind"] == "obj" for x in items), "print_job_count": jobs,
                          "total_bytes": sum(x["size_bytes"] for x in items) + job_bytes}}

    async def save_upload(upload):
        path, total = store.temp / f"{uuid.uuid4().hex}.part", 0
        try:
            with path.open("xb") as f:
                while chunk := await upload.read(1024**2):
                    total += len(chunk)
                    if total > store.max_upload:
                        raise ModelError(f"File exceeds {store.max_upload // 1024**2} MiB.")
                    f.write(chunk)
            if not total:
                raise ModelError("The uploaded file is empty.")
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @app.post("/api/models/upload", dependencies=[Depends(browser_access)])
    async def upload(request: Request):
        items, errors = [], []
        async with request.form(max_files=10, max_fields=5) as form:
            files = form.getlist("files")
            if not files or any(not isinstance(f, UploadFile) for f in files):
                raise HTTPException(400, "Choose one or more STL, OBJ or model 3MF files.")
            collection = clean_text(form.get("collection", ""), 80)
            for f in files:
                name = safe_filename(f.filename)
                try:
                    temp = await save_upload(f)
                    item = await run_in_threadpool(store.add_model, temp, name, collection)
                    items.append(item)
                except (ModelError, ValueError) as e:
                    errors.append({"filename": name, "message": str(e)[:400]})
        return {"items": items, "errors": errors}

    @app.get("/api/models/{mid}", dependencies=[Depends(browser_access)])
    def model(mid: str):
        row = store.row(mid)
        item = store.model_json(row)
        # Detail views must not mistake an old blank/corrupt PNG for a saved
        # preview: the browser can then fall back to its successful 3D view.
        item["thumbnail_ready"] = store.usable_thumbnail(store.thumbs / f"{mid}.png")
        # Read ZIP directory metadata only. The original remains unchanged and
        # existing databases require no migration for this preview hint.
        model_xml_bytes = 0
        large_preview = row["triangle_count"] > 1_000_000 or row["size_bytes"] > 64 * 1024**2
        if row["kind"] == "3mf":
            try:
                with zipfile.ZipFile(store.model_path(row)) as archive:
                    model_xml_bytes = sum(entry.file_size for entry in archive.infolist()
                                          if entry.filename.lower().endswith(".model"))
                large_preview = large_preview or model_xml_bytes > 64 * 1024**2
            except (OSError, zipfile.BadZipFile):
                large_preview = True
        return item | {"model_xml_bytes": model_xml_bytes, "large_preview": large_preview}

    @app.get("/api/models/{mid}/file", dependencies=[Depends(browser_access)])
    def model_file(mid: str):
        row = store.row(mid)
        return FileResponse(store.model_path(row), filename=row["filename"], media_type="application/octet-stream")

    @app.post("/api/models/{mid}/studio", dependencies=[Depends(browser_access)])
    async def prepare_studio(mid: str):
        try:
            return await run_in_threadpool(store.prepare_studio, mid)
        except OSError as exc:
            log.warning("Studio preparation failed for model %s (%s)", mid, type(exc).__name__)
            raise HTTPException(400, "The Studio copy could not be prepared. Download the original and open it in Bambu Studio, or try again after checking free storage.") from exc

    @app.get("/api/models/{mid}/studio/{filename}", dependencies=[Depends(browser_access)])
    def studio_file(mid: str, filename: str):
        row = store.row(mid)
        if filename != store.studio_filename(row):
            raise HTTPException(404, "Studio file not found.")
        path = store.studio_path(row)
        if not path.is_file():
            raise HTTPException(409, "Open the model in Layer Library and choose Open in Bambu Studio to prepare its file first.")
        # A desktop application has no browser session cookie. This has the
        # same trusted-LAN read access as an original model download.
        return FileResponse(path, filename=filename, media_type="model/3mf")

    @app.put("/api/models/{mid}", dependencies=[Depends(browser_access)])
    def edit(mid: str, body: EditModel):
        store.row(mid)
        tags = list(dict.fromkeys(clean_text(t, 40) for t in body.tags if clean_text(t, 40)))
        with store.db() as db:
            db.execute("UPDATE models SET title=?,collection=?,tags=?,notes=?,updated_at=? WHERE id=?",
                       (clean_text(body.title, 160), clean_text(body.collection, 80), json.dumps(tags), clean_text(body.notes, 10000), now(), mid))
        return store.model_json(store.row(mid))

    @app.put("/api/models/{mid}/featured", dependencies=[Depends(browser_access)])
    def set_featured(mid: str, body: FeaturedModel):
        with store.db() as db:
            updated = db.execute("UPDATE models SET featured=?,updated_at=? WHERE id=? AND deleted_at IS NULL",
                                 (int(body.featured), now(), mid))
            if not updated.rowcount:
                raise HTTPException(404, "Model not found.")
            row = db.execute("SELECT * FROM models WHERE id=?", (mid,)).fetchone()
        return store.model_json(row)

    @app.delete("/api/models/{mid}", dependencies=[Depends(browser_access)])
    def trash(mid: str):
        store.row(mid)
        with store.db() as db:
            if db.execute("SELECT 1 FROM transfers t JOIN jobs j ON j.id=t.job_id WHERE j.model_id=? AND t.status IN ('queued','sending')", (mid,)).fetchone():
                raise HTTPException(409, "Wait for this model's printer transfer to finish before moving it to Trash.")
            db.execute("UPDATE models SET deleted_at=?,updated_at=? WHERE id=?", (now(), now(), mid))
        return {"ok": True}

    @app.get("/api/trash", dependencies=[Depends(browser_access)])
    def trash_list():
        with store.db() as db:
            rows = db.execute("SELECT * FROM models WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC").fetchall()
        return {"items": [store.model_json(r) for r in rows]}

    @app.post("/api/models/{mid}/restore", dependencies=[Depends(browser_access)])
    def restore(mid: str):
        store.row(mid, True)
        with store.db() as db:
            db.execute("UPDATE models SET deleted_at=NULL,updated_at=? WHERE id=?", (now(), mid))
        return store.model_json(store.row(mid))

    @app.get("/api/models/{mid}/thumbnail", dependencies=[Depends(browser_access)])
    def thumbnail(mid: str):
        try:
            path = store.ensure_thumbnail(mid)
        except ModelError as exc:
            raise HTTPException(422, str(exc)) from exc
        return FileResponse(path, media_type="image/png")

    @app.post("/api/models/{mid}/thumbnail/rebuild", dependencies=[Depends(browser_access)])
    def rebuild_thumbnail(mid: str):
        try:
            store.ensure_thumbnail(mid, force=True)
        except ModelError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True, "thumbnail_ready": True,
                "thumbnail_url": f"/api/models/{mid}/thumbnail?v={time.time_ns()}"}

    @app.post("/api/models/{mid}/thumbnail", dependencies=[Depends(browser_access)])
    async def save_thumbnail(mid: str, request: Request):
        store.row(mid)
        data = await request.body()
        if len(data) > 8 * 1024**2:
            raise HTTPException(413, "Thumbnail exceeds 8 MiB.")
        try:
            await run_in_threadpool(store.save_browser_thumbnail, mid, data)
        except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
            raise HTTPException(400, "Use a valid PNG thumbnail smaller than four megapixels.")
        return {"ok": True, "thumbnail_ready": True, "thumbnail_url": f"/api/models/{mid}/thumbnail"}

    @app.get("/api/models/{mid}/jobs", dependencies=[Depends(browser_access)])
    def jobs(mid: str):
        store.row(mid)
        with store.db() as db:
            rows = db.execute("SELECT * FROM jobs WHERE model_id=? AND deleted_at IS NULL ORDER BY created_at DESC", (mid,)).fetchall()
        return {"items": [store.job_json(r) for r in rows]}

    @app.post("/api/models/{mid}/jobs", dependencies=[Depends(browser_access)])
    async def upload_job(mid: str, request: Request):
        store.row(mid)
        async with request.form(max_files=1, max_fields=2) as form:
            f = form.get("file")
            if not isinstance(f, UploadFile) or not str(f.filename).lower().endswith(".gcode.3mf"):
                raise HTTPException(400, "Choose a .gcode.3mf exported after slicing for the Bambu P2S.")
            temp = await save_upload(f)
            try:
                metadata = await run_in_threadpool(inspect_job, temp)
                jid, size = uuid.uuid4().hex, temp.stat().st_size
                os.replace(temp, store.files / f"{jid}.gcode.3mf")
                with store.db() as db:
                    db.execute("INSERT INTO jobs(id,model_id,filename,size_bytes,created_at,metadata) VALUES(?,?,?,?,?,?)",
                               (jid, mid, safe_filename(f.filename), size, now(), json.dumps(metadata)))
                return store.job_json(store.job_row(jid))
            except ModelError as e:
                raise HTTPException(400, str(e))
            finally:
                temp.unlink(missing_ok=True)

    @app.get("/api/jobs/{jid}/file", dependencies=[Depends(browser_access)])
    def job_file(jid: str):
        row = store.job_row(jid)
        return FileResponse(store.files / f"{jid}.gcode.3mf", filename=row["filename"], media_type="application/octet-stream")

    def studio_job(jid):
        row = store.job_row(jid)
        if not json.loads(row["metadata"])["compatible"]:
            raise HTTPException(400, "This sliced file does not identify a Bambu P2S. Open the original model in Studio and re-slice with your P2S profile.")
        return row

    @app.post("/api/jobs/{jid}/studio", dependencies=[Depends(browser_access)])
    def prepare_studio_job(jid: str):
        row = studio_job(jid)
        filename = store.studio_filename(row)
        return {"download_url": f"/api/jobs/{jid}/studio/{filename}", "filename": filename,
                "converted": False, "sliced": True, "size_bytes": row["size_bytes"],
                "message": "Open this sliced file in Bambu Studio and check your P2S, filament and plate before choosing Print or Send."}

    @app.get("/api/jobs/{jid}/studio/{filename}", dependencies=[Depends(browser_access)])
    def studio_job_file(jid: str, filename: str):
        row = studio_job(jid)
        if filename != store.studio_filename(row):
            raise HTTPException(404, "Studio print file not found.")
        return FileResponse(store.files / f"{jid}.gcode.3mf", filename=filename, media_type="model/3mf")

    @app.delete("/api/jobs/{jid}", dependencies=[Depends(browser_access)])
    def delete_job(jid: str):
        store.job_row(jid)
        with store.db() as db:
            if db.execute("SELECT 1 FROM transfers WHERE job_id=? AND status IN ('queued','sending')", (jid,)).fetchone():
                raise HTTPException(409, "Wait for this transfer to finish before removing the job.")
            db.execute("UPDATE jobs SET deleted_at=? WHERE id=?", (now(), jid))
        return {"ok": True}

    @app.get("/api/printer", dependencies=[Depends(browser_access)])
    def printer_config():
        return store.public_printer()

    @app.put("/api/printer", dependencies=[Depends(browser_access)])
    def printer_save(body: PrinterConfig):
        with store.lock:
            old = store.printer_settings()
            with store.db() as db:
                if db.execute("SELECT 1 FROM transfers WHERE status IN ('queued','sending')").fetchone():
                    raise HTTPException(409, "Wait for the printer transfer to finish before changing its connection settings.")
            if body.mode == "cloud":
                # Studio owns cloud sign-in. No cloud credentials, IP address,
                # access code or connectivity claim belongs to this mode.
                config = dict(old)
                config.update(mode="cloud", name=clean_text(body.name, 80) or "Bambu P2S")
            else:
                try:
                    host = validate_host(body.host.strip() or old.get("host", ""))
                except ValueError as e:
                    raise HTTPException(400, str(e))
                if body.serial and not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", body.serial.strip()):
                    raise HTTPException(400, "The serial number should contain letters and numbers only.")
                code = body.access_code.strip() or old.get("access_code", "")
                if not code:
                    raise HTTPException(400, "Enter the printer's LAN access code, or choose Bambu Studio to keep using cloud services without a code.")
                config = {"mode": "lan", "name": clean_text(body.name, 80) or "Bambu P2S", "host": host,
                          "serial": body.serial.strip(), "access_code": code}
                if host == old.get("host") and code == old.get("access_code"):
                    config["fingerprint"] = old.get("fingerprint", "")
            store.save_printer(config)
        return store.public_printer()

    @app.post("/api/printer/test", dependencies=[Depends(browser_access)])
    async def printer_test():
        from .printer import test_connection
        config = store.printer_settings()
        if config.get("mode") != "lan":
            raise HTTPException(409, "Bambu Studio manages the cloud connection. Open a model in Studio and use the printer signed into your Bambu account.")
        if not config.get("host") or not config.get("access_code"):
            raise HTTPException(400, "Save the printer's IP address and LAN access code first.")
        try:
            outcome = await run_in_threadpool(test_connection, config["host"], config["access_code"], config.get("fingerprint"))
            if not outcome.get("ok"):
                raise ValueError(outcome.get("message", "The printer could not be verified."))
            with store.lock:
                current = store.printer_settings()
                if (current.get("mode") != "lan" or current.get("host") != config["host"] or current.get("access_code") != config["access_code"]
                        or current.get("fingerprint") != config.get("fingerprint")):
                    raise ValueError("Printer settings changed while testing; test again.")
                current["fingerprint"] = outcome["certificate_fingerprint"]
                store.save_printer(current)
            return outcome
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e).replace(config["access_code"], "[redacted]")[:500])

    @app.post("/api/printer/trust/reset", dependencies=[Depends(browser_access)])
    def reset_printer_trust():
        with store.lock:
            config = store.printer_settings()
            if not config.get("host"):
                raise HTTPException(400, "No printer is configured.")
            with store.db() as db:
                if db.execute("SELECT 1 FROM transfers WHERE status IN ('queued','sending')").fetchone():
                    raise HTTPException(409, "Wait for the printer transfer to finish before resetting trust.")
            config.pop("fingerprint", None)
            store.save_printer(config)
        return {"ok": True, "message": "Saved certificate cleared. Test the connection again to trust this printer."}

    @app.get("/api/printer/status", dependencies=[Depends(browser_access)])
    def printer_status():
        public = store.public_printer()
        message = ("Bambu Studio manages your cloud connection. Keep the P2S in its normal cloud-connected mode; no LAN access code is needed here. Printer status and sending are available in Studio."
                   if public["mode"] == "cloud" else
                   "This optional local connection only transfers files to printer storage. Availability depends on printer firmware. If it is unavailable, use Bambu Studio while keeping cloud services enabled. Live telemetry and remote start are not enabled here.")
        return {"available": False, "state": "managed_in_studio" if public["mode"] == "cloud" else "unavailable",
                "message": message, **public}

    @app.post("/api/jobs/{jid}/send", dependencies=[Depends(browser_access)])
    def send(jid: str, body: SendJob):
        if body.start_print:
            raise HTTPException(400, "This app sends files to printer storage only. Start printing from the printer screen.")
        job = store.job_row(jid)
        if not json.loads(job["metadata"])["compatible"]:
            raise HTTPException(400, "The sliced file does not identify a Bambu P2S. Re-slice with a P2S printer profile and export the plate sliced file.")
        tid, stamp = uuid.uuid4().hex, now()
        with store.lock, store.db() as db:
            config = store.printer_settings()
            if config.get("mode") != "lan":
                raise HTTPException(409, "Use Bambu Studio to send through your cloud-connected P2S. Layer Library does not upload to Bambu cloud directly.")
            if not config.get("fingerprint"):
                raise HTTPException(400, "Save and successfully test the printer connection before sending files.")
            if db.execute("SELECT 1 FROM transfers WHERE status IN ('queued','sending')").fetchone():
                raise HTTPException(409, "A transfer is already running. Wait for it to finish.")
            db.execute("INSERT INTO transfers VALUES(?,?,?,?,?,?,?)", (tid, jid, "queued", 0, "Waiting to send…", stamp, stamp))
        store.pool.submit(store.transfer, tid, dict(job), config)
        return {"transfer_id": tid, "status": "queued"}

    @app.get("/api/transfers/{tid}", dependencies=[Depends(browser_access)])
    def transfer_status(tid: str):
        with store.db() as db:
            row = db.execute("SELECT status,progress,message FROM transfers WHERE id=?", (tid,)).fetchone()
        if not row:
            raise HTTPException(404, "Transfer not found.")
        return dict(row)

    @app.exception_handler(ModelError)
    async def model_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    return app


app = create_app()
