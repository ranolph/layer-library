"""Small, deterministic mesh previews without a browser, GPU or external assets.

STL and OBJ triangles are streamed twice: once to fit the complete geometry,
then through a bounded software z-buffer. No triangles are sampled or dropped
to meet a face-count target. A time/work limit fails clearly instead of saving
a partial or blank picture. Existing thumbnails survive every failure.
"""
from __future__ import annotations

import io
import math
import os
from pathlib import Path
import posixpath
import struct
import time
import uuid
import zipfile
import zlib

import numpy as np
from PIL import Image, ImageFilter, UnidentifiedImageError
from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException

from .mesh import ModelError, checked_zip


WIDTH, HEIGHT = 640, 480
SUPERSAMPLE = 1.5
BACKGROUND = (236, 234, 227)
MAX_SECONDS = 45.0
MAX_FILE_BYTES = 300 * 1024**2
MAX_TRIANGLES = 10_000_000
MAX_RASTER_SAMPLES = 200_000_000
CHUNK_TRIANGLES = 8192
MAX_IMAGE_BYTES = 8 * 1024**2
MAX_IMAGE_PIXELS = 4_000_000
_STL_DTYPE = np.dtype([("normal", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")])


class _Budget:
    def __init__(self, seconds=MAX_SECONDS):
        self.deadline = time.monotonic() + seconds
        self.samples = 0

    def check(self, samples=0):
        self.samples += int(samples)
        if self.samples > MAX_RASTER_SAMPLES or time.monotonic() > self.deadline:
            raise ModelError("This model is too complex for an automatic thumbnail within the preview limit. Open its 3D preview to save a picture instead.")


def _stl_triangles(path: Path, budget=None):
    """Read complete binary/ASCII STL grammar in fixed-size geometry chunks."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(84)
        count = struct.unpack("<I", header[80:84])[0] if len(header) == 84 else 0
        if count and size == 84 + count * 50:
            if count > MAX_TRIANGLES:
                raise ModelError("The STL exceeds the automatic preview triangle limit.")
            left = count
            while left:
                if budget is not None:
                    budget.check()
                n = min(left, CHUNK_TRIANGLES)
                data = stream.read(n * 50)
                if len(data) != n * 50:
                    raise ModelError("The binary STL is incomplete.")
                triangles = np.frombuffer(data, dtype=_STL_DTYPE)["v"].astype(np.float64)
                if not np.isfinite(triangles).all():
                    raise ModelError("The STL contains invalid coordinates.")
                yield triangles
                left -= n
            return
    state, vertices, triangles, total = "solid", [], [], 0
    try:
        with path.open("rb") as stream:
            line_number = 0
            while raw := stream.readline(4097):
                line_number += 1
                if budget is not None and line_number % 4096 == 0:
                    budget.check()
                if len(raw) > 4096:
                    raise ModelError("The ASCII STL contains an overlong line.")
                p = raw.decode("ascii").strip().split()
                if not p:
                    continue
                if state in ("solid", "done") and p[0].lower() == "solid":
                    state = "facet"
                elif state == "facet" and p[0].lower() == "endsolid":
                    state = "done"
                elif state == "facet" and len(p) == 5 and p[:2] == ["facet", "normal"]:
                    if not all(math.isfinite(float(v)) for v in p[2:]):
                        raise ModelError("The STL contains invalid normals.")
                    state = "loop"
                elif state == "loop" and p == ["outer", "loop"]:
                    vertices, state = [], "vertex"
                elif state == "vertex" and len(p) == 4 and p[0] == "vertex":
                    point = [float(v) for v in p[1:]]
                    if not all(math.isfinite(v) for v in point):
                        raise ModelError("The STL contains invalid coordinates.")
                    vertices.append(point)
                    if len(vertices) == 3:
                        state = "endloop"
                elif state == "endloop" and p == ["endloop"]:
                    state = "endfacet"
                elif state == "endfacet" and p == ["endfacet"]:
                    triangles.append(vertices)
                    total += 1
                    if total > MAX_TRIANGLES:
                        raise ModelError("The STL exceeds the automatic preview triangle limit.")
                    state = "facet"
                    if len(triangles) == CHUNK_TRIANGLES:
                        yield np.asarray(triangles, dtype=np.float64)
                        triangles = []
                else:
                    raise ModelError("This is not a valid binary or ASCII STL.")
        if state != "done" or total == 0:
            raise ModelError("The ASCII STL is incomplete or contains no triangles.")
        if triangles:
            yield np.asarray(triangles, dtype=np.float64)
    except (UnicodeError, ValueError, OverflowError) as exc:
        if isinstance(exc, ModelError):
            raise
        raise ModelError("This is not a valid STL file.") from exc


def _measure(factory, budget):
    lo, hi, count = np.full(3, np.inf), np.full(3, -np.inf), 0
    for triangles in factory():
        budget.check()
        if triangles.ndim != 3 or triangles.shape[1:] != (3, 3) or not np.isfinite(triangles).all():
            raise ModelError("The model contains invalid triangle coordinates.")
        count += len(triangles)
        if count > MAX_TRIANGLES:
            raise ModelError("The model exceeds the automatic preview triangle limit.")
        lo = np.minimum(lo, triangles.min(axis=(0, 1)))
        hi = np.maximum(hi, triangles.max(axis=(0, 1)))
    with np.errstate(over="ignore", invalid="ignore"):
        extent = hi - lo
    if not count or not np.isfinite(extent).all() or extent.max() <= 0:
        raise ModelError("The model has no visible finite mesh geometry.")
    if extent.max() > 1_000_000:
        raise ModelError("Model coordinates are too large; export in millimetres.")
    return lo, hi, count


def _unit(vector):
    value = np.asarray(vector, dtype=np.float64)
    return value / np.linalg.norm(value)


def _scatter(zbuffer, colors, indexes, depths, face_colors):
    """Resolve all triangles hitting a pixel, including collisions in one batch."""
    if not len(indexes):
        return
    np.maximum.at(zbuffer, indexes, depths)
    selected = depths >= zbuffer[indexes]
    # Equal-depth fragments lie on the same surface. Last wins deterministically.
    colors[indexes[selected]] = face_colors[selected]


def _rasterize(factory, lo, hi, budget, view):
    width, height = int(WIDTH * SUPERSAMPLE), int(HEIGHT * SUPERSAMPLE)
    view = _unit(view)
    right = _unit(np.cross(view, (0, 0, 1)))
    up = _unit(np.cross(right, view))
    basis = np.stack((right, up, view), axis=1)
    extent = hi - lo
    world_scale = extent.max()
    # Normalize before projection; off-origin and very small models then have
    # the same stable coverage and depth precision as ordinary printing files.
    corners = np.asarray([[x, y, z] for x in (0, extent[0])
                          for y in (0, extent[1]) for z in (0, extent[2])]) / world_scale
    projected_corners = corners @ basis
    low, high = projected_corners.min(axis=0), projected_corners.max(axis=0)
    span = high - low
    scale = min(width * .82 / max(span[0], 1e-12), height * .82 / max(span[1], 1e-12))
    offset = np.array((width / 2 - (low[0] + high[0]) * scale / 2,
                       height / 2 + (low[1] + high[1]) * scale / 2))
    zbuffer = np.full(width * height, -np.inf, dtype=np.float32)
    colors = np.full((width * height, 3), BACKGROUND, dtype=np.uint8)
    light = _unit((-.45, -.85, 1.4))
    base = np.array((194, 170, 130), dtype=np.float64)
    total, visible_faces = 0, 0
    for raw in factory():
        budget.check()
        total += len(raw)
        triangles = (np.asarray(raw, dtype=np.float64) - lo) / world_scale
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        keep = lengths > 1e-18
        if not keep.any():
            continue
        triangles, normals, lengths = triangles[keep], normals[keep], lengths[keep]
        normals /= lengths[:, None]
        toward = normals @ view
        normals[toward < 0] *= -1
        intensity = .48 + .42 * np.maximum(normals @ light, 0) + .10 * np.abs(toward)
        face_colors = np.clip(base * intensity[:, None] + 13, 0, 255).astype(np.uint8)
        p = triangles @ basis
        p[:, :, 0] = p[:, :, 0] * scale + offset[0]
        p[:, :, 1] = -p[:, :, 1] * scale + offset[1]
        # Pixel centers, rather than pixel corners, avoid holes between faces.
        mins = np.ceil(p[:, :, :2].min(axis=1) - .5).astype(np.int64)
        maxs = np.floor(p[:, :, :2].max(axis=1) - .5).astype(np.int64)
        mins = np.maximum(mins, (0, 0))
        maxs = np.minimum(maxs, (width - 1, height - 1))
        spans = maxs - mins + 1
        edge0, edge1 = p[:, 1, :2] - p[:, 0, :2], p[:, 2, :2] - p[:, 0, :2]
        denominator = edge0[:, 0] * edge1[:, 1] - edge0[:, 1] * edge1[:, 0]
        valid = (spans > 0).all(axis=1) & (np.abs(denominator) > 1e-15)
        budget.check(np.prod(np.maximum(spans[valid], 0), axis=1).sum())
        visible_faces += int(valid.sum())
        # The vast majority of triangles in a large mesh touch a small pixel
        # rectangle. Rasterize these in vectorized batches, never one Python
        # call per triangle, so multi-million-face files remain practical.
        small = valid & (spans <= 4).all(axis=1)
        indexes = np.flatnonzero(small)
        if len(indexes):
            ps, minimum, maximum = p[indexes], mins[indexes], maxs[indexes]
            a, b, den = edge0[indexes], edge1[indexes], denominator[indexes]
            for dy in range(int(spans[indexes, 1].max())):
                for dx in range(int(spans[indexes, 0].max())):
                    xx, yy = minimum[:, 0] + dx, minimum[:, 1] + dy
                    qx, qy = xx + .5 - ps[:, 0, 0], yy + .5 - ps[:, 0, 1]
                    u = (qx * b[:, 1] - qy * b[:, 0]) / den
                    v = (a[:, 0] * qy - a[:, 1] * qx) / den
                    inside = ((xx <= maximum[:, 0]) & (yy <= maximum[:, 1]) &
                              (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9))
                    if not inside.any():
                        continue
                    depth = ps[:, 0, 2] + u * (ps[:, 1, 2] - ps[:, 0, 2]) + v * (ps[:, 2, 2] - ps[:, 0, 2])
                    _scatter(zbuffer, colors, (yy[inside] * width + xx[inside]),
                             depth[inside].astype(np.float32), face_colors[indexes[inside]])
        # Coarser meshes need larger rectangles. Tile each rectangle to cap
        # temporary arrays; the total candidate-pixel budget still applies.
        for ordinal, index in enumerate(np.flatnonzero(valid & ~small)):
            if ordinal % 64 == 0:
                budget.check()
            x0, y0 = mins[index]
            x1, y1 = maxs[index]
            ps, a, b, den = p[index], edge0[index], edge1[index], denominator[index]
            for top in range(int(y0), int(y1) + 1, 32):
                yy, xx = np.mgrid[top:min(top + 32, int(y1) + 1), int(x0):int(x1) + 1]
                qx, qy = xx + .5 - ps[0, 0], yy + .5 - ps[0, 1]
                u = (qx * b[1] - qy * b[0]) / den
                v = (a[0] * qy - a[1] * qx) / den
                inside = (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9)
                if not inside.any():
                    continue
                depth = (ps[0, 2] + u[inside] * (ps[1, 2] - ps[0, 2]) +
                         v[inside] * (ps[2, 2] - ps[0, 2])).astype(np.float32)
                pixels = yy[inside] * width + xx[inside]
                selected = depth >= zbuffer[pixels]
                zbuffer[pixels[selected]] = depth[selected]
                colors[pixels[selected]] = face_colors[index]
    mask = np.isfinite(zbuffer).reshape(height, width)
    if int(mask.sum()) < 8:
        raise ModelError("The model has no visible surface from this preview angle.")
    # A small soft shadow follows the actual rendered silhouette.
    mesh = Image.fromarray(colors.reshape(height, width, 3), "RGB")
    silhouette = Image.fromarray((mask.astype(np.uint8) * 80), "L").filter(ImageFilter.GaussianBlur(8))
    background = Image.new("RGB", (width, height), BACKGROUND)
    shadow = Image.new("RGB", (width, height), (155, 154, 147))
    background.paste(shadow, (0, 7), silhouette)
    background.paste(mesh, (0, 0), Image.fromarray(mask.astype(np.uint8) * 255, "L"))
    return background.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS), total, visible_faces


def _embedded_3mf(path, budget):
    """Use an embedded picture only; never follow material or external URLs."""
    with checked_zip(path) as archive:
        candidates = []
        names = {name.lower(): name for name in archive.namelist()}
        relation_name = names.get("_rels/.rels")
        if relation_name and archive.getinfo(relation_name).file_size <= 65536:
            try:
                root = ET.fromstring(archive.read(relation_name))
            except (ET.ParseError, DefusedXmlException, ValueError):
                root = []
            for relation in root:
                if (relation.attrib.get("Type", "").endswith("/thumbnail") and
                        relation.attrib.get("TargetMode", "Internal").lower() != "external"):
                    target = relation.attrib.get("Target", "")
                    if (target and ":" not in target and "\\" not in target and
                            not target.startswith("//") and ".." not in target.split("/")):
                        candidates.append(posixpath.normpath(target.lstrip("/")))
        candidates.extend(("Metadata/thumbnail.png", "Metadata/plate_1.png",
                           "Metadata/plate_1_small.png", "Thumbnails/thumbnail.png",
                           "Metadata/thumbnail.jpg"))
        for candidate in dict.fromkeys(candidates):
            budget.check()
            name = names.get(candidate.lower())
            if not name or archive.getinfo(name).file_size > MAX_IMAGE_BYTES:
                continue
            try:
                with Image.open(io.BytesIO(archive.read(name))) as source:
                    if source.format not in ("PNG", "JPEG") or source.width * source.height > MAX_IMAGE_PIXELS:
                        continue
                    source.load()
                    source = source.convert("RGBA")
                    # Check the source before letterboxing, otherwise a blank
                    # white producer picture plus beige margins looks nonblank.
                    source_values = np.asarray(source)
                    if (np.max(np.ptp(source_values.astype(np.int16), axis=(0, 1))) <= 2 or
                            source_values[:, :, 3].max() == 0):
                        continue
                    source.thumbnail((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
                    output = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
                    output.paste(source, ((WIDTH - source.width) // 2, (HEIGHT - source.height) // 2), source)
                    # A uniformly blank producer thumbnail is not a success.
                    values = np.asarray(output)
                    if int(values.max(axis=(0, 1)).max()) - int(values.min(axis=(0, 1)).min()) <= 1:
                        continue
                    if np.max(np.ptp(values.astype(np.int16), axis=(0, 1))) <= 2:
                        continue
                    return output
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
                continue
    raise ModelError("This 3MF has no usable embedded preview image. Open its 3D preview to save a thumbnail.")


def _atomic_save(image, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / ("." + destination.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            image.save(stream, "PNG", optimize=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def render_thumbnail(path: Path, kind: str, destination: Path):
    """Atomically save a 640×480 PNG and return a small rendering report.

    Raises ModelError on failure; never saves partial/placeholder images.
    Callers should serialize this with mesh inspection to bound aggregate
    memory. OBJ's compact vertex store is bounded by its parser; the renderer's
    own arrays are fixed-size or contain one 8,192-triangle chunk.
    """
    path, destination = Path(path), Path(destination)
    budget = _Budget(MAX_SECONDS)
    try:
        if path.resolve() == destination.resolve():
            raise ModelError("The preview destination must be separate from the original model.")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ModelError("This file exceeds the automatic thumbnail size limit.")
        kind = str(kind).lower().lstrip(".")
        if kind == "3mf":
            picture = _embedded_3mf(path, budget)
            report = {"source": "embedded", "sampled": False}
        else:
            if kind == "stl":
                factory = lambda: _stl_triangles(path, budget)
            elif kind == "obj":
                from .obj_mesh import iter_obj_triangles
                factory = lambda: iter_obj_triangles(path, chunk_size=CHUNK_TRIANGLES, check_budget=budget.check)
            else:
                raise ModelError("Automatic previews support STL, OBJ and embedded 3MF pictures.")
            lo, hi, count = _measure(factory, budget)
            try:
                picture, rendered, visible = _rasterize(factory, lo, hi, budget, (.85, -1.1, .72))
            except ModelError as exc:
                # An unusual planar mesh may be viewed exactly edge-on.
                if "no visible surface" not in str(exc):
                    raise
                picture, rendered, visible = _rasterize(factory, lo, hi, budget, (-.9, -1.2, 1.35))
            if rendered != count:
                raise ModelError("The model changed while its preview was being generated. Try again.")
            report = {"source": "mesh", "sampled": False, "triangle_count": count,
                      "rendered_triangle_count": rendered, "projected_triangle_count": visible}
        budget.check()
        _atomic_save(picture, destination)
        return report | {"width": WIDTH, "height": HEIGHT}
    except ModelError:
        raise
    except (OSError, ValueError, OverflowError, zipfile.BadZipFile, zlib.error) as exc:
        raise ModelError("The model's preview could not be generated from its stored geometry.") from exc
