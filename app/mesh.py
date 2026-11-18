"""Bounded, non-executing inspection of uploaded STL and 3MF files.

Model coordinates are reported in millimetres. STL has no unit field; its
coordinates are interpreted as millimetres.
"""
from __future__ import annotations

from array import array
import json
import math
import posixpath
import re
import struct
import zipfile
import zlib
from pathlib import Path

import numpy as np
from defusedxml import ElementTree as ET


class ModelError(ValueError):
    pass


def result(lo, hi, triangles):
    lo, hi = np.asarray(lo), np.asarray(hi)
    dims = hi - lo
    if triangles < 1 or not np.isfinite(dims).all() or np.any(dims < 0):
        raise ModelError("The file has no valid finite mesh geometry.")
    if float(dims.max()) > 1_000_000:
        raise ModelError("Model coordinates are too large; export in millimetres.")
    return {"dimensions": dict(zip(("x", "y", "z"), [round(float(v), 4) for v in dims])),
            "triangle_count": int(triangles),
            "fit_p2s": bool(np.all(dims <= 256.0001)),
            "units": "mm"}


def inspect_stl(path: Path):
    size = path.stat().st_size
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    with path.open("rb") as f:
        header = f.read(84)
        count = struct.unpack("<I", header[80:84])[0] if len(header) == 84 else 0
        if count and size == 84 + count * 50:
            dtype = np.dtype([("normal", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")])
            remaining = count
            while remaining:
                n = min(40000, remaining)
                chunk = f.read(n * 50)
                if len(chunk) != n * 50:
                    raise ModelError("The binary STL is incomplete.")
                vertices = np.frombuffer(chunk, dtype=dtype)["v"].reshape(-1, 3)
                if not np.isfinite(vertices).all():
                    raise ModelError("The STL contains invalid coordinates.")
                lo = np.minimum(lo, vertices.min(axis=0))
                hi = np.maximum(hi, vertices.max(axis=0))
                remaining -= n
            return result(lo, hi, count)
    # Parse the complete ASCII grammar, rather than accepting arbitrary text
    # containing three lines beginning with 'vertex'. Keep memory bounded.
    count, state, vertex_count = 0, "solid", 0
    try:
        with path.open("rt", encoding="ascii", errors="strict") as f:
            for raw in f:
                if len(raw) > 4096:
                    raise ModelError("The ASCII STL contains an overlong line.")
                line = raw.strip()
                if not line:
                    continue
                p = line.split()
                if state == "solid" and p[0].lower() == "solid":
                    state = "facet"
                elif state == "facet" and p[0].lower() == "endsolid":
                    state = "done"
                elif state == "facet" and len(p) == 5 and p[:2] == ["facet", "normal"]:
                    if not all(math.isfinite(float(v)) for v in p[2:]):
                        raise ModelError("The STL contains invalid normals.")
                    state = "loop"
                elif state == "loop" and p == ["outer", "loop"]:
                    vertex_count, state = 0, "vertex"
                elif state == "vertex" and len(p) == 4 and p[0] == "vertex":
                    v = np.array([float(x) for x in p[1:]])
                    if not np.isfinite(v).all():
                        raise ModelError("The STL contains invalid coordinates.")
                    lo, hi = np.minimum(lo, v), np.maximum(hi, v)
                    vertex_count += 1
                    if vertex_count == 3:
                        state = "endloop"
                elif state == "endloop" and p == ["endloop"]:
                    state = "endfacet"
                elif state == "endfacet" and p == ["endfacet"]:
                    count, state = count + 1, "facet"
                elif state == "done" and p[0].lower() == "solid":
                    state = "facet"
                else:
                    raise ModelError("This is not a valid binary or ASCII STL.")
        if state != "done":
            raise ModelError("The ASCII STL is incomplete.")
    except (UnicodeError, OverflowError) as e:
        raise ModelError("This is not a valid STL file.") from e
    return result(lo, hi, count)


def checked_zip(path: Path):
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise ModelError("The 3MF file is not a readable ZIP archive.") from e
    entries = z.infolist()
    if len(entries) > 3000 or sum(i.file_size for i in entries) > 800 * 1024**2:
        z.close()
        raise ModelError("The 3MF expands beyond this app's inspection limit (800 MiB / 3,000 entries).")
    names = set()
    for i in entries:
        n = i.filename
        if (n in names or n.startswith(("/", "\\")) or "\\" in n or
            ".." in n.split("/") or i.flag_bits & 1 or
            (i.file_size > 2 * 1024**2 and i.file_size > 500 * max(i.compress_size, 1))):
            z.close()
            raise ModelError("The 3MF contains unsafe, duplicate, encrypted or excessively compressed entries.")
        names.add(n)
    return z


def local(tag):
    return tag.rsplit("}", 1)[-1]


# Resource budgets apply to the complete archive, not separately to each mesh.
# Eight million float64 XYZ vertices occupy 183 MiB; faces are counted and
# validated as they arrive, without retaining their indexes or an XML tree.
MAX_3MF_MODEL_BYTES = 512 * 1024**2
MAX_3MF_MODEL_DOCUMENTS = 100
MAX_3MF_VERTICES = 8_000_000
MAX_3MF_TRIANGLES = 10_000_000
MAX_3MF_OBJECTS = 10_000
MAX_3MF_COMPONENTS = 10_000
MAX_3MF_BUILD_ITEMS = 10_000
MAX_3MF_VISITS = 10_000
MAX_3MF_COMPONENT_DEPTH = 32
MAX_3MF_ASSEMBLED_VERTICES = 20_000_000
MAX_3MF_XML_DEPTH = 64
MAX_3MF_XML_ATTRIBUTES = 64
MAX_3MF_XML_ATTRIBUTE_CHARS = 16_384
MAX_3MF_XML_NAME_CHARS = 1024
MAX_3MF_XML_NAMES = 2048
MAX_3MF_XML_NAMESPACES = 10_000
MAX_3MF_XML_ELEMENTS = 20_000_000
MAX_3MF_XML_TOKEN_BYTES = 256 * 1024
XML_READ_BYTES = 64 * 1024
TRANSFORM_VERTEX_CHUNK = 65_536
UNITS_3MF = {"micron": .001, "millimeter": 1., "centimeter": 10.,
             "inch": 25.4, "foot": 304.8, "meter": 1000.}


def transform(text):
    matrix = np.eye(4)
    if text:
        if len(text) > 2048:
            raise ModelError("A 3MF transform is invalid.")
        try:
            parts = text.split()
            if len(parts) != 12:
                raise ValueError("A transform requires twelve numbers.")
            a = np.asarray(parts, dtype=np.float64)
        except (TypeError, ValueError, OverflowError) as e:
            raise ModelError("A 3MF transform is invalid.") from e
        if not np.isfinite(a).all():
            raise ModelError("A 3MF transform is invalid.")
        a = a.reshape(4, 3)
        matrix[:3, :3], matrix[:3, 3] = a[:3].T, a[3]
    return matrix


class _ThreeMFBudget:
    def __init__(self):
        self.model_bytes = 0
        self.vertices = 0
        self.triangles = 0
        self.objects = 0
        self.components = 0
        self.build_items = 0
        self.elements = 0
        self.namespaces = 0
        self.names = set()


def _reference_attributes(attrs):
    # Keep only fields needed for assembly. Large optional producer metadata
    # must not be retained once its streaming callback returns.
    result = {}
    for key in ("objectid", "transform", "printable"):
        if key in attrs:
            value = attrs[key]
            limit = 2048 if key == "transform" else 128
            if len(value) > limit:
                raise ModelError("A 3MF component or build reference is too long.")
            result[key] = value
    for key, value in attrs.items():
        if local(key) == "path":
            if len(value) > 2048:
                raise ModelError("A 3MF component path is too long.")
            result["path"] = value
            break
    return result


class _ThreeMFModelTarget:
    """SAX-like ElementTree target: no Element objects or XML text are kept."""

    def __init__(self, budget):
        self.budget = budget
        self.depth = 0
        self.unit = None
        self.objects = {}
        self.build = []
        self.object_id = None
        self.object_depth = 0
        self.vertices = None
        self.triangles = 0
        self.max_vertex_index = -1
        self.components = None

    def start(self, tag, attrs):
        budget = self.budget
        self.depth += 1
        budget.elements += 1
        if self.depth > MAX_3MF_XML_DEPTH or budget.elements > MAX_3MF_XML_ELEMENTS:
            raise ModelError("The 3MF XML is too deeply nested or complex.")
        if len(tag) > MAX_3MF_XML_NAME_CHARS or len(attrs) > MAX_3MF_XML_ATTRIBUTES:
            raise ModelError("The 3MF XML contains excessive names or attributes.")
        if any(len(k) > MAX_3MF_XML_NAME_CHARS or len(v) > MAX_3MF_XML_ATTRIBUTE_CHARS
               for k, v in attrs.items()):
            raise ModelError("The 3MF XML contains an oversized name or attribute.")
        budget.names.add(tag)
        budget.names.update(attrs)
        if len(budget.names) > MAX_3MF_XML_NAMES:
            raise ModelError("The 3MF XML contains too many distinct names.")
        key = local(tag)
        if self.depth == 1:
            unit = attrs.get("unit", "millimeter")
            if key != "model" or unit not in UNITS_3MF:
                raise ModelError("The 3MF model uses an unsupported unit or structure.")
            self.unit = UNITS_3MF[unit]
        if key == "object":
            oid = attrs.get("id")
            if self.object_id is not None:
                raise ModelError("The 3MF contains improperly nested objects.")
            if not oid or len(oid) > 128 or oid in self.objects:
                raise ModelError("The 3MF contains duplicate or missing object IDs.")
            budget.objects += 1
            if budget.objects > MAX_3MF_OBJECTS:
                raise ModelError("The 3MF contains too many objects.")
            self.object_id, self.object_depth = oid, self.depth
            self.vertices = array("d")
            self.triangles, self.max_vertex_index = 0, -1
            self.components = []
        elif key == "vertex" and self.object_id is not None:
            budget.vertices += 1
            if budget.vertices > MAX_3MF_VERTICES:
                raise ModelError("The 3MF exceeds the aggregate vertex inspection limit.")
            sx, sy, sz = (attrs.get(k, "nan") for k in ("x", "y", "z"))
            if max(len(sx), len(sy), len(sz)) > 128:
                raise ModelError("The 3MF contains an invalid coordinate.")
            x, y, z = float(sx), float(sy), float(sz)
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                raise ModelError("The 3MF contains invalid coordinates.")
            self.vertices.extend((x, y, z))
        elif key == "triangle" and self.object_id is not None:
            budget.triangles += 1
            if budget.triangles > MAX_3MF_TRIANGLES:
                raise ModelError("The 3MF exceeds the aggregate triangle inspection limit.")
            indexes = (attrs.get(k, "-1") for k in ("v1", "v2", "v3"))
            for value in indexes:
                if len(value) > 20:
                    raise ModelError("The 3MF references a missing vertex.")
                index = int(value)
                if index < 0:
                    raise ModelError("The 3MF references a missing vertex.")
                if index > self.max_vertex_index:
                    self.max_vertex_index = index
            self.triangles += 1
        elif key == "component" and self.object_id is not None:
            budget.components += 1
            if budget.components > MAX_3MF_COMPONENTS:
                raise ModelError("The 3MF contains too many components.")
            self.components.append(_reference_attributes(attrs))
        elif key == "item":
            budget.build_items += 1
            if budget.build_items > MAX_3MF_BUILD_ITEMS:
                raise ModelError("The 3MF contains too many build items.")
            self.build.append(_reference_attributes(attrs))

    def end(self, tag):
        if self.object_id is not None and self.depth == self.object_depth:
            if self.max_vertex_index >= len(self.vertices) // 3:
                raise ModelError("The 3MF references a missing vertex.")
            # The NumPy view owns a reference to the compact array buffer;
            # there is no second copy of the complete object's coordinates.
            vertices = np.frombuffer(self.vertices, dtype=np.float64).reshape(-1, 3)
            self.objects[self.object_id] = (vertices, self.triangles, self.components)
            self.object_id, self.vertices, self.components = None, None, None
        self.depth -= 1

    def data(self, text):
        pass

    def comment(self, text):
        pass

    def pi(self, target, text):
        pass

    def start_ns(self, prefix, uri):
        self.budget.namespaces += 1
        if (self.budget.namespaces > MAX_3MF_XML_NAMESPACES or
                len(prefix or "") > MAX_3MF_XML_NAME_CHARS or
                len(uri or "") > MAX_3MF_XML_NAME_CHARS):
            raise ModelError("The 3MF XML contains excessive namespace declarations.")

    def end_ns(self, prefix):
        pass

    def close(self):
        if self.unit is None or self.depth:
            raise ModelError("The 3MF model XML is incomplete.")
        return self.objects, self.build, self.unit


def _read_3mf_model(archive, name, budget):
    target = _ThreeMFModelTarget(budget)
    parser = ET.DefusedXMLParser(target=target, forbid_dtd=True,
                                forbid_entities=True, forbid_external=True)
    read_bytes, consumed_bytes = 0, 0
    with archive.open(name) as stream:
        while chunk := stream.read(XML_READ_BYTES):
            read_bytes += len(chunk)
            budget.model_bytes += len(chunk)
            if budget.model_bytes > MAX_3MF_MODEL_BYTES:
                raise ModelError("3MF model XML exceeds the 512 MiB inspection limit.")
            parser.feed(chunk)
            # Expat can buffer an unfinished attribute/comment before calling
            # our target. Bound that pending token as well as parsed values,
            # including with deferred token reparsing on recent Python builds.
            position = parser.parser.CurrentByteIndex
            if position >= 0:
                consumed_bytes = max(consumed_bytes, position)
            if read_bytes - consumed_bytes > MAX_3MF_XML_TOKEN_BYTES:
                raise ModelError("The 3MF XML contains an oversized tag, attribute or comment.")
    return parser.close()


def inspect_3mf(path: Path):
    with checked_zip(path) as z:
        model_names = [n for n in z.namelist() if n.lower().endswith(".model")]
        if not model_names:
            raise ModelError("This 3MF has no model geometry. Attach sliced files under Print jobs.")
        if (len(model_names) > MAX_3MF_MODEL_DOCUMENTS or
                sum(z.getinfo(n).file_size for n in model_names) > MAX_3MF_MODEL_BYTES):
            raise ModelError("3MF model XML exceeds the 512 MiB / 100-document inspection limit.")
        docs, budget = {}, _ThreeMFBudget()
        try:
            for name in model_names:
                docs[name] = _read_3mf_model(z, name, budget)
        except ModelError:
            raise
        except Exception as e:
            raise ModelError("The 3MF model XML is invalid or uses unsafe XML features.") from e

        main = next((n for n in model_names if n.lower() == "3d/3dmodel.model"), model_names[0])
        lo, hi, total, visits = np.full(3, np.inf), np.full(3, -np.inf), 0, 0
        assembled_vertices = 0

        def visit(doc, attrs, parent, stack):
            nonlocal lo, hi, total, visits, assembled_vertices
            visits += 1
            if visits > MAX_3MF_VISITS or len(stack) > MAX_3MF_COMPONENT_DEPTH:
                raise ModelError("The 3MF component tree is too large or recursive.")
            source_doc = doc
            refpath = attrs.get("path")
            if refpath:
                ref = refpath.lstrip("/") if refpath.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(doc), refpath))
                # Bambu production extensions commonly use a /3D/... absolute package path.
                if ref not in docs and refpath.lstrip("/") in docs:
                    ref = refpath.lstrip("/")
                doc = ref
            key = (doc, attrs.get("objectid"))
            if key in stack or doc not in docs or key[1] not in docs[doc][0]:
                raise ModelError("The 3MF has a cyclic or missing component reference.")
            # The assembly transform is expressed in one shared document
            # unit. Mixed-unit external references need a separate conversion
            # at each boundary; reject them rather than report wrong bounds.
            if docs[source_doc][2] != docs[doc][2]:
                raise ModelError("Referenced 3MF model documents use different units. "
                                 "Re-export the assembly with all documents in millimetres.")
            with np.errstate(over="ignore", invalid="ignore"):
                mat = parent @ transform(attrs.get("transform"))
            if not np.isfinite(mat).all():
                raise ModelError("A composed 3MF transform contains invalid coordinates.")
            vertices, count, components = docs[doc][0][key[1]]
            if count:
                total += count
                assembled_vertices += len(vertices)
                if total > MAX_3MF_TRIANGLES:
                    raise ModelError("The assembled 3MF exceeds ten million triangles.")
                if assembled_vertices > MAX_3MF_ASSEMBLED_VERTICES:
                    raise ModelError("The assembled 3MF exceeds the vertex inspection limit.")
                # Keep rotation/shear bounds exact by visiting the actual
                # vertices, but never allocate a second complete mesh array.
                for start in range(0, len(vertices), TRANSFORM_VERTEX_CHUNK):
                    with np.errstate(over="ignore", invalid="ignore"):
                        transformed = vertices[start:start + TRANSFORM_VERTEX_CHUNK] @ mat[:3, :3].T
                        transformed += mat[:3, 3]
                        transformed *= docs[doc][2]
                    if not np.isfinite(transformed).all():
                        raise ModelError("The 3MF contains invalid transformed coordinates.")
                    lo = np.minimum(lo, transformed.min(axis=0))
                    hi = np.maximum(hi, transformed.max(axis=0))
            for component in components:
                visit(doc, component, mat, stack + [key])

        build = docs[main][1]
        if not build:
            raise ModelError("The 3MF contains no build items.")
        for item in build:
            if item.get("printable", "true").lower() != "false":
                visit(main, item, np.eye(4), [])
        return result(lo, hi, total)


def inspect_job(path: Path):
    try:
        return _inspect_job(path)
    except ModelError:
        raise
    except (zipfile.BadZipFile, EOFError, RuntimeError, OSError, zlib.error) as e:
        raise ModelError("The sliced 3MF is damaged or uses an unsupported ZIP format. Export it again from Bambu Studio.") from e


def _inspect_job(path: Path):
    with checked_zip(path) as z:
        names = z.namelist()
        plates = sorted(n for n in names if re.fullmatch(r"Metadata/plate_[1-9]\d*\.gcode", n))
        if not plates:
            raise ModelError("Use Bambu Studio's Export plate sliced file to create a .gcode.3mf containing plate G-code.")
        model, nozzle = "", ""
        settings_name = next((n for n in names if n.lower() == "metadata/project_settings.config"), None)
        if settings_name:
            if z.getinfo(settings_name).file_size > 2 * 1024**2:
                raise ModelError("Slicer settings are unexpectedly large.")
            try:
                settings = json.loads(z.read(settings_name))
                if not isinstance(settings, dict):
                    raise ModelError("Slicer settings must be a JSON object.")
                for key in ("printer_model", "printer_settings_id", "printer_variant"):
                    v = settings.get(key)
                    if isinstance(v, str) and v:
                        model = model or v
                        if "P2S" in v.upper():
                            model = v
                            break
                value = settings.get("nozzle_diameter", "")
                nozzle = str(value[0] if isinstance(value, list) and value else value)
            except (ValueError, TypeError):
                pass
        for p in plates:
            if z.getinfo(p).file_size < 32:
                raise ModelError("The sliced plate contains no usable G-code.")
            with z.open(p) as f:
                header = f.read(131072).decode("utf-8", errors="replace")
            if not model:
                match = re.search(r"^;\s*(?:printer_model|printer_settings_id)\s*[:=]\s*(.+)$", header, re.M | re.I)
                if match:
                    model = match.group(1).strip()[:150]
            if not nozzle:
                match = re.search(r"^;\s*nozzle_diameter\s*[:=]\s*([\d.]+)", header, re.M | re.I)
                if match:
                    nozzle = match.group(1)
        return {"printer_model": model or "Unknown", "nozzle_diameter": nozzle or "Unknown",
                "plate_names": plates, "compatible": bool(re.search(r"\bP2S\b", model, re.I)),
                "status": "ready" if re.search(r"\bP2S\b", model, re.I) else "check_printer"}
