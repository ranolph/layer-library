"""Prepare geometry-only 3MF handoff files without changing stored originals.

The package uses the 3MF Core model namespace and OPC content types/start-part
relationship. Coordinates, triangle order and winding are retained exactly as
read by the STL/OBJ parsers; exact coincident coordinates share vertex indexes.
No rescaling, centring, mesh repair, material loading or slicing is performed.
Consequently an open/non-manifold input still needs any repairs its slicer
requires. The original remains the authoritative downloadable model.

Mesh deduplication uses a private disk-backed SQLite database with a bounded
cache, and triangle indexes are spooled to disk. XML is written directly into
the ZIP stream, never accumulated as a complete document in memory.
"""
from __future__ import annotations

from contextlib import closing
import os
from pathlib import Path
import sqlite3
import stat
import struct
import tempfile
import time
import zipfile
import zlib

import numpy as np

from .mesh import ModelError, result
from .obj_mesh import iter_obj_triangles
from .thumbnails import _stl_triangles


MAX_SOURCE_BYTES = 250 * 1024**2
MAX_VERTICES = 8_000_000
MAX_TRIANGLES = 10_000_000
MAX_XML_BYTES = 512 * 1024**2
MAX_EXPANDED_BYTES = 800 * 1024**2
MAX_OUTPUT_BYTES = 500 * 1024**2
MAX_TEMP_BYTES = 1536 * 1024**2
MAX_SECONDS = 180.0
CHUNK_TRIANGLES = 8192
XML_BATCH = 4096
SQLITE_CACHE_KIB = 8192
MODEL_PART = "3D/3dmodel.model"
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
START_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
CONTENT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
MODEL_CONTENT_TYPE = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
FALLBACK = " Download the original model and open it in Bambu Studio instead."

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<Types xmlns="{CONTENT_NS}">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    f'<Override PartName="/{MODEL_PART}" ContentType="{MODEL_CONTENT_TYPE}"/>'
    '</Types>\n'
).encode("utf-8")
RELATIONSHIPS = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<Relationships xmlns="{REL_NS}">'
    f'<Relationship Id="rel0" Target="/{MODEL_PART}" Type="{START_REL}"/>'
    '</Relationships>\n'
).encode("utf-8")
XML_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    f'<model xmlns="{CORE_NS}" unit="millimeter" xml:lang="en-US">\n'
    '<metadata name="Application">Layer Library</metadata>\n'
    '<resources><object id="1" type="model" name="Model"><mesh><vertices>\n'
).encode("utf-8")
XML_MIDDLE = b'</vertices><triangles>\n'
XML_FOOTER = b'</triangles></mesh></object></resources><build><item objectid="1"/></build></model>\n'


def _failure(message):
    return ModelError(message.rstrip() + ("" if message.rstrip().endswith(FALLBACK.strip()) else FALLBACK))


class _Budget:
    def __init__(self):
        self.deadline = time.monotonic() + MAX_SECONDS
        self.xml_bytes = 0
        self.timed_out = False

    def check(self, samples=0):
        # _stl_triangles and iter_obj_triangles share this callback contract.
        if self.timed_out or time.monotonic() > self.deadline:
            self.timed_out = True
            raise _failure("Preparing this model for Bambu Studio exceeded the conversion time limit.")

    def sql_progress(self):
        if time.monotonic() > self.deadline:
            self.timed_out = True
            return 1
        return 0

    def write_xml(self, stream, data):
        self.check()
        self.xml_bytes += len(data)
        if self.xml_bytes > MAX_XML_BYTES:
            raise _failure("The converted 3MF model would exceed the 512 MiB XML limit.")
        if self.xml_bytes + len(CONTENT_TYPES) + len(RELATIONSHIPS) > MAX_EXPANDED_BYTES:
            raise _failure("The converted 3MF would exceed the expanded archive limit.")
        stream.write(data)


def _source_signature(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise _failure("The stored model is not a regular file.")
    if not 0 < info.st_size <= MAX_SOURCE_BYTES:
        raise _failure("The source model is empty or exceeds the 250 MiB conversion limit.")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _temp_budget(db_path, face_bytes, package_path=None):
    total = db_path.stat().st_size + face_bytes
    if package_path is not None and package_path.exists():
        total += package_path.stat().st_size
    if total > MAX_TEMP_BYTES:
        raise _failure("Preparing this model exceeded the temporary storage limit.")


def _connect(path, budget):
    database = sqlite3.connect(path)
    try:
        database.execute("PRAGMA journal_mode=OFF")
        database.execute("PRAGMA synchronous=OFF")
        database.execute("PRAGMA temp_store=FILE")
        database.execute("PRAGMA mmap_size=0")
        database.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
        database.execute("CREATE TABLE vertices(id INTEGER PRIMARY KEY, coordinates BLOB NOT NULL UNIQUE)")
        database.execute("CREATE TEMP TABLE batch(position INTEGER PRIMARY KEY, coordinates BLOB NOT NULL UNIQUE)")
        database.set_progress_handler(budget.sql_progress, 10000)
        return database
    except Exception:
        database.close()
        raise


def _collect(source, kind, database, db_path, face_path, budget):
    factory = (_stl_triangles(source, budget) if kind == "stl" else
               iter_obj_triangles(source, chunk_size=CHUNK_TRIANGLES, check_budget=budget.check))
    triangle_count = vertex_count = 0
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    with face_path.open("wb") as faces:
        for triangles in factory:
            budget.check()
            triangles = np.asarray(triangles, dtype=np.float64)
            if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or not np.isfinite(triangles).all():
                raise _failure("The stored model contains invalid triangle coordinates.")
            triangle_count += len(triangles)
            if triangle_count > MAX_TRIANGLES:
                raise _failure("The converted model would exceed the 10 million triangle limit.")
            points = triangles.reshape(-1, 3).copy()
            if not len(points):
                continue
            lo, hi = np.minimum(lo, points.min(axis=0)), np.maximum(hi, points.max(axis=0))
            # +0 and -0 denote the same geometric coordinate; canonicalize them
            # before exact byte-based deduplication without rounding any value.
            points[points == 0] = 0.0
            unique, inverse = np.unique(points, axis=0, return_inverse=True)
            keys = [row.astype("<f8", copy=False).tobytes() for row in unique]
            database.execute("DELETE FROM batch")
            database.executemany("INSERT INTO batch(position,coordinates) VALUES(?,?)", enumerate(keys))
            database.execute("INSERT OR IGNORE INTO vertices(coordinates) SELECT coordinates FROM batch ORDER BY position")
            vertex_count = database.execute("SELECT COALESCE(MAX(id),0) FROM vertices").fetchone()[0]
            if vertex_count > MAX_VERTICES:
                raise _failure("The converted model would exceed the 8 million vertex limit.")
            # Fresh SQLite INTEGER PRIMARY KEY values are contiguous here:
            # there are no deletes or AUTOINCREMENT allocations in vertices.
            rows = database.execute("SELECT v.id-1 FROM batch b JOIN vertices v ON v.coordinates=b.coordinates ORDER BY b.position")
            indexes = np.fromiter((row[0] for row in rows), dtype=np.uint32, count=len(unique))
            faces.write(indexes[inverse].astype("<u4", copy=False).tobytes())
            database.commit()
            _temp_budget(db_path, faces.tell())
            budget.check()
    if vertex_count < 3:
        raise _failure("The stored model has fewer than three distinct vertices.")
    metadata = result(lo, hi, triangle_count)
    return metadata | {"vertex_count": int(vertex_count)}


def _write_package(database, db_path, face_path, package_path, budget):
    # Low compression has bounded CPU cost and avoids making a highly repetitive
    # mesh exceed the library's existing 500:1 ZIP expansion-ratio guard.
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=1, allowZip64=True) as package:
        package.writestr("[Content_Types].xml", CONTENT_TYPES)
        package.writestr("_rels/.rels", RELATIONSHIPS)
        with package.open(MODEL_PART, "w") as model:
            budget.write_xml(model, XML_HEADER)
            cursor = database.execute("SELECT coordinates FROM vertices ORDER BY id")
            while rows := cursor.fetchmany(XML_BATCH):
                lines = []
                for row in rows:
                    x, y, z = struct.unpack("<3d", row[0])
                    lines.append(f'<vertex x="{x:.17g}" y="{y:.17g}" z="{z:.17g}"/>\n')
                budget.write_xml(model, "".join(lines).encode("ascii"))
                _temp_budget(db_path, face_path.stat().st_size, package_path)
            budget.write_xml(model, XML_MIDDLE)
            with face_path.open("rb") as faces:
                while data := faces.read(XML_BATCH * 12):
                    if len(data) % 12:
                        raise _failure("The temporary triangle data was incomplete.")
                    indexes = np.frombuffer(data, dtype="<u4").reshape(-1, 3)
                    lines = [f'<triangle v1="{a}" v2="{b}" v3="{c}"/>\n' for a, b, c in indexes]
                    budget.write_xml(model, "".join(lines).encode("ascii"))
                    _temp_budget(db_path, face_path.stat().st_size, package_path)
            budget.write_xml(model, XML_FOOTER)
    budget.check()
    if package_path.stat().st_size > MAX_OUTPUT_BYTES:
        raise _failure("The converted 3MF exceeds the output size limit.")
    # Validate package metadata, without loading or reparsing the complete mesh.
    with zipfile.ZipFile(package_path) as package:
        entries = package.infolist()
        if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES or any(
                entry.file_size > 2 * 1024**2 and entry.file_size > 500 * max(entry.compress_size, 1)
                for entry in entries):
            raise _failure("The converted archive exceeds the library's safe 3MF limits.")
    with package_path.open("rb") as stream:
        os.fsync(stream.fileno())


def export_model_3mf(source: Path, kind: str, destination: Path):
    """Create an atomic geometry-only STL/OBJ-to-3MF handoff for Bambu Studio.

    ``ModelError`` includes a manual-download fallback on every normal failure.
    Existing destination bytes survive failures; concurrent conversions use
    isolated temporary files and replace only a fully closed, valid package.
    Callers should share their mesh-inspection lock to limit aggregate memory.
    """
    source, destination = Path(source), Path(destination)
    budget = _Budget()
    try:
        kind = str(kind).lower().lstrip(".")
        if kind not in ("stl", "obj"):
            raise _failure("This conversion accepts STL and OBJ models only.")
        if source.resolve() == destination.resolve() or (destination.exists() and source.samefile(destination)):
            raise _failure("The converted file must be separate from the original model.")
        signature = _source_signature(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".studio-export-", dir=destination.parent) as temporary:
            directory = Path(temporary)
            db_path, face_path = directory / "vertices.sqlite3", directory / "triangles.bin"
            package_path = directory / "model.3mf"
            with closing(_connect(db_path, budget)) as database:
                metadata = _collect(source, kind, database, db_path, face_path, budget)
                _write_package(database, db_path, face_path, package_path, budget)
            budget.check()
            if _source_signature(source) != signature:
                raise _failure("The original model changed during conversion. Try again.")
            metadata |= {"size_bytes": package_path.stat().st_size,
                         "model_xml_bytes": budget.xml_bytes, "source_kind": kind}
            os.replace(package_path, destination)
        return metadata
    except ModelError as exc:
        raise _failure(str(exc)) from exc
    except (OSError, ValueError, OverflowError, sqlite3.Error, zipfile.BadZipFile, zlib.error) as exc:
        budget.check()
        raise _failure("The model could not be prepared for Bambu Studio.") from exc
