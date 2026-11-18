"""Bounded, geometry-only Wavefront OBJ inspection and triangle streaming.

OBJ does not define a physical unit: polygon XYZ coordinates are interpreted
as millimetres, as for STL. Optional vertex weights belong to rational curves
and surfaces; they do not rescale polygon XYZ. Vertex colours, normal/UV data,
object/group names and material declarations are accepted, but neither material
files nor any other referenced file, URL or command is opened or executed.

The two public entry points share the same parser and triangulation. An iterator
consumer must exhaust it to validate the whole file. It yields independent
float64 arrays of shape (N, 3, 3), with at most ``chunk_size`` triangles each.
"""
from __future__ import annotations

from array import array
import math
from pathlib import Path
import re

import numpy as np

from .mesh import ModelError, result


MAX_OBJ_BYTES = 250 * 1024**2
MAX_OBJ_VERTICES = 8_000_000
MAX_OBJ_ATTRIBUTE_VERTICES = 16_000_000
MAX_OBJ_TRIANGLES = 10_000_000
MAX_OBJ_FACE_VERTICES = 256
MAX_OBJ_PHYSICAL_LINE = 64 * 1024
MAX_OBJ_LOGICAL_LINE = 128 * 1024
MAX_OBJ_LINES = 20_000_000
MAX_OBJ_TRIANGULATION_WORK = 20_000_000
MAX_OBJ_ABS_COORDINATE = 1e12
_EPS = 1e-12
_INDEX = re.compile(r"[+-]?[0-9]{1,16}\Z")
_FREEFORM = frozenset(("cstype", "deg", "bmat", "step", "curv", "curv2", "surf",
                       "parm", "trim", "hole", "scrv", "sp", "end", "con"))


def _error(message, line=None):
    suffix = f" (line {line})" if line is not None else ""
    return ModelError(message + suffix)


def _lines(path, check_budget=None):
    """Read physical and continued logical lines without an unbounded readline."""
    path = Path(path)
    if check_budget is not None:
        check_budget()
    if path.stat().st_size > MAX_OBJ_BYTES:
        raise _error("The OBJ exceeds this app's 250 MiB inspection limit.")
    pending = bytearray()
    start, total, line_number = 1, 0, 0
    with path.open("rb") as stream:
        while True:
            raw = stream.readline(MAX_OBJ_PHYSICAL_LINE + 1)
            if not raw:
                break
            line_number += 1
            total += len(raw)
            if check_budget is not None and line_number % 4096 == 0:
                check_budget()
            if total > MAX_OBJ_BYTES or line_number > MAX_OBJ_LINES:
                raise _error("The OBJ is too large or contains too many lines.")
            if len(raw) > MAX_OBJ_PHYSICAL_LINE:
                raise _error("The OBJ contains an overlong line.", line_number)
            if b"\x00" in raw:
                raise _error("The OBJ must be a text polygon mesh, not binary data.", line_number)
            if line_number == 1:
                raw = raw.removeprefix(b"\xef\xbb\xbf")
            # Non-ASCII names/comments are harmless. Replacement characters in a
            # numeric record will subsequently fail numeric/index validation.
            value = raw.split(b"#", 1)[0].rstrip()
            continued = value.endswith(b"\\")
            if continued:
                value = value[:-1].rstrip()
            if not pending:
                start = line_number
            if len(pending) + len(value) + 1 > MAX_OBJ_LOGICAL_LINE:
                raise _error("The OBJ contains an overlong continued line.", start)
            pending.extend(value)
            if continued:
                pending.extend(b" ")
                continue
            text = pending.decode("utf-8", errors="replace").strip()
            pending.clear()
            if text:
                yield start, text.split()
    if pending:
        raise _error("The OBJ ends in an incomplete continued line.", start)


def _numbers(parts, line):
    try:
        values = [float(value) for value in parts]
    except (ValueError, OverflowError) as exc:
        raise _error("The OBJ contains an invalid number.", line) from exc
    if not all(math.isfinite(value) for value in values):
        raise _error("The OBJ contains non-finite coordinates or attributes.", line)
    return values


def _index(text, count, label, line):
    if not _INDEX.fullmatch(text):
        raise _error(f"The OBJ contains an invalid {label} index.", line)
    value = int(text)
    if not value:
        raise _error("OBJ indices start at 1; zero is not a valid index.", line)
    value = value - 1 if value > 0 else count + value
    if value < 0 or value >= count:
        raise _error(f"The OBJ references a missing {label}; define it before the face.", line)
    return value


def _face_indices(parts, vertex_count, uv_count, normal_count, line):
    if len(parts) < 3:
        raise _error("An OBJ face needs at least three vertices.", line)
    if len(parts) > MAX_OBJ_FACE_VERTICES:
        raise _error(f"An OBJ face has more than {MAX_OBJ_FACE_VERTICES} corners; export triangulated faces.", line)
    indices = []
    for token in parts:
        fields = token.split("/")
        if len(fields) > 3 or not fields[0]:
            raise _error("The OBJ contains an invalid face reference.", line)
        indices.append(_index(fields[0], vertex_count, "vertex", line))
        if len(fields) == 2:
            if not fields[1]:
                raise _error("The OBJ contains an incomplete texture reference.", line)
            _index(fields[1], uv_count, "texture coordinate", line)
        elif len(fields) == 3:
            if fields[1]:
                _index(fields[1], uv_count, "texture coordinate", line)
            if not fields[2]:
                raise _error("The OBJ contains an incomplete normal reference.", line)
            _index(fields[2], normal_count, "normal", line)
    return indices


def _cross(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, point):
    return (min(a[0], b[0]) - _EPS <= point[0] <= max(a[0], b[0]) + _EPS and
            min(a[1], b[1]) - _EPS <= point[1] <= max(a[1], b[1]) + _EPS)


def _intersects(a, b, c, d):
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    if ((ab_c > _EPS and ab_d < -_EPS) or (ab_c < -_EPS and ab_d > _EPS)) and \
       ((cd_a > _EPS and cd_b < -_EPS) or (cd_a < -_EPS and cd_b > _EPS)):
        return True
    return ((abs(ab_c) <= _EPS and _on_segment(a, b, c)) or
            (abs(ab_d) <= _EPS and _on_segment(a, b, d)) or
            (abs(cd_a) <= _EPS and _on_segment(c, d, a)) or
            (abs(cd_b) <= _EPS and _on_segment(c, d, b)))


class _Work:
    def __init__(self):
        self.used = 0

    def add(self, count, line):
        self.used += count
        if self.used > MAX_OBJ_TRIANGULATION_WORK:
            raise _error("The OBJ polygons are too complex to inspect; export triangulated faces.", line)


def _triangulate(indices, vertices, work, line):
    """Triangulate a simple polygon, retaining original 3D vertices/winding.

    Dominant-plane projection also accepts ordinary mildly non-planar quads.
    Ear clipping, rather than a triangle fan, correctly handles concave faces.
    Degenerate zero-area faces contribute no renderable triangles.
    """
    if len(indices) == 3:
        # Preserve ordinary and degenerate source triangles. This is deliberately
        # constant-work: the typical large OBJ is already triangulated.
        return [tuple(indices)]
    points = np.asarray([vertices[index * 3:index * 3 + 3] for index in indices], dtype=np.float64)
    kept = [0]
    for i in range(1, len(indices)):
        if not np.array_equal(points[i], points[kept[-1]]):
            kept.append(i)
    if len(kept) > 1 and np.array_equal(points[kept[0]], points[kept[-1]]):
        kept.pop()
    if len(kept) < 3:
        return []
    points = points[kept]
    indices = [indices[i] for i in kept]
    relative = points - points[0]
    scale = float(np.abs(relative).max())
    if not scale:
        return []
    relative /= scale
    normal = np.cross(relative, np.roll(relative, -1, axis=0)).sum(axis=0)
    axis = int(np.argmax(np.abs(normal)))
    if float(np.abs(normal).max()) <= _EPS:
        # A bow-tie can have zero signed area despite non-collinear corners.
        spans = np.ptp(relative, axis=0)
        axis = int(np.argmin(spans))
        projected = np.delete(relative, axis, axis=1)
        if any(abs(_cross(projected[0], projected[i], projected[i + 1])) > _EPS
               for i in range(1, len(projected) - 1)):
            raise _error("An OBJ polygon intersects itself or has ambiguous winding; export triangulated faces.", line)
        return []
    points2 = np.delete(relative, axis, axis=1).tolist()
    size = len(points2)
    work.add(size * (size - 3) // 2, line)
    for i in range(size):
        for j in range(i + 2, size):
            if i == 0 and j == size - 1:
                continue
            if _intersects(points2[i], points2[(i + 1) % size], points2[j], points2[(j + 1) % size]):
                raise _error("An OBJ polygon intersects itself; export triangulated faces.", line)
    # Remove collinear interior corners before clipping; this avoids zero-area
    # ears and keeps the visible polygon boundary unchanged.
    active = list(range(size))
    changed = True
    while changed and len(active) > 3:
        changed = False
        for at in range(len(active)):
            work.add(1, line)
            before, current, after = active[at - 1], active[at], active[(at + 1) % len(active)]
            if abs(_cross(points2[before], points2[current], points2[after])) <= _EPS:
                del active[at]
                changed = True
                break
    signed_area = sum(points2[active[i]][0] * points2[active[(i + 1) % len(active)]][1] -
                      points2[active[(i + 1) % len(active)]][0] * points2[active[i]][1]
                      for i in range(len(active)))
    direction = 1 if signed_area > 0 else -1
    triangles = []
    while len(active) > 3:
        found = False
        for at, current in enumerate(active):
            before, after = active[at - 1], active[(at + 1) % len(active)]
            a, b, c = points2[before], points2[current], points2[after]
            work.add(1, line)
            if direction * _cross(a, b, c) <= _EPS:
                continue
            blocked = False
            for candidate in active:
                if candidate in (before, current, after):
                    continue
                work.add(1, line)
                p = points2[candidate]
                if (direction * _cross(a, b, p) >= -_EPS and
                        direction * _cross(b, c, p) >= -_EPS and
                        direction * _cross(c, a, p) >= -_EPS):
                    blocked = True
                    break
            if blocked:
                continue
            triangles.append((indices[before], indices[current], indices[after]))
            del active[at]
            found = True
            break
        if not found:
            raise _error("An OBJ polygon cannot be triangulated; export triangulated faces.", line)
    if len(active) == 3 and abs(_cross(*(points2[i] for i in active))) > _EPS:
        triangles.append(tuple(indices[i] for i in active))
    return triangles


def iter_obj_triangles(path: Path, chunk_size: int = 8192, *, check_budget=None):
    """Yield checked float64 triangle chunks; exhaust to validate the whole OBJ."""
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or not 1 <= chunk_size <= 65_536:
        raise ValueError("chunk_size must be an integer between 1 and 65536")
    vertices = array("d")
    uv_count = normal_count = triangle_count = 0
    chunk = array("d")
    work = _Work()
    freeform = False
    for line, parts in _lines(path, check_budget):
        keyword, values = parts[0], parts[1:]
        if keyword == "v":
            if len(values) not in (3, 4, 6, 7, 8):
                raise _error("An OBJ vertex needs XYZ, optionally a weight and/or colour values.", line)
            numbers = _numbers(values, line)
            if any(abs(value) > MAX_OBJ_ABS_COORDINATE for value in numbers[:3]):
                raise _error("OBJ coordinates are too large; export in millimetres near the model origin.", line)
            if len(vertices) // 3 >= MAX_OBJ_VERTICES:
                raise _error("The OBJ contains too many vertices (limit 8 million).", line)
            vertices.extend(numbers[:3])
        elif keyword == "vt":
            if not 1 <= len(values) <= 3:
                raise _error("An OBJ texture coordinate needs one to three numbers.", line)
            _numbers(values, line)
            uv_count += 1
            if uv_count > MAX_OBJ_ATTRIBUTE_VERTICES:
                raise _error("The OBJ contains too many texture coordinates.", line)
        elif keyword == "vn":
            if len(values) != 3:
                raise _error("An OBJ normal needs three numbers.", line)
            _numbers(values, line)
            normal_count += 1
            if normal_count > MAX_OBJ_ATTRIBUTE_VERTICES:
                raise _error("The OBJ contains too many normals.", line)
        elif keyword == "vp":
            if not 1 <= len(values) <= 3:
                raise _error("An OBJ parameter vertex needs one to three numbers.", line)
            _numbers(values, line)
            freeform = True
        elif keyword == "f":
            indices = _face_indices(values, len(vertices) // 3, uv_count, normal_count, line)
            if triangle_count + len(indices) - 2 > MAX_OBJ_TRIANGLES:
                raise _error("The OBJ contains too many triangles (limit 10 million).", line)
            triangles = _triangulate(indices, vertices, work, line)
            triangle_count += len(triangles)
            for triangle in triangles:
                for index in triangle:
                    chunk.extend(vertices[index * 3:index * 3 + 3])
                if len(chunk) >= chunk_size * 9:
                    yield np.frombuffer(chunk, dtype=np.float64).reshape(-1, 3, 3)
                    chunk = array("d")
        elif keyword in _FREEFORM:
            freeform = True
        # Other records (including groups, materials, points and polylines) are
        # inert metadata or non-surface elements. Never follow a reference.
    if not triangle_count:
        if freeform:
            raise _error("This OBJ contains curves or free-form surfaces but no polygon mesh. Export a mesh OBJ with faces.")
        raise _error("The OBJ has no polygon faces to display. Export a mesh OBJ with faces.")
    if chunk:
        yield np.frombuffer(chunk, dtype=np.float64).reshape(-1, 3, 3)


def inspect_obj(path: Path, *, include_bounds: bool = False, check_budget=None):
    """Return the standard library metadata, optionally with raw XYZ bounds."""
    lo, hi, triangles = np.full(3, np.inf), np.full(3, -np.inf), 0
    for chunk in iter_obj_triangles(path, check_budget=check_budget):
        points = chunk.reshape(-1, 3)
        lo, hi = np.minimum(lo, points.min(axis=0)), np.maximum(hi, points.max(axis=0))
        triangles += len(chunk)
    metadata = result(lo, hi, triangles)
    if include_bounds:
        metadata["bounds"] = [lo.tolist(), hi.tolist()]
    return metadata
