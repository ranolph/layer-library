"""Regression tests for large, streamed 3MF model inspection.

Run from the package directory:
    .venv/bin/python -m unittest discover -s tests -p test_large_3mf.py -v

Standalone measurement, without the API or a browser:
    .venv/bin/python tests/test_large_3mf.py --benchmark

The large fixture is generated incrementally into a temporary ZIP.  It is a
closed torus with 524,288 vertices and 1,048,576 triangles, not a file padded
with comments or repetitive whitespace.  No fixture or user model is bundled.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from app import mesh


CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRODUCTION_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
TETRA_VERTICES = ((0, 0, 0), (2, 0, 0), (0, 3, 0), (0, 0, 4))
TETRA_TRIANGLES = ((0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3))


def mesh_object(object_id="1", vertices=TETRA_VERTICES, triangles=TETRA_TRIANGLES):
    vertex_xml = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices)
    triangle_xml = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in triangles)
    return (
        f'<object id="{object_id}" type="model"><mesh><vertices>{vertex_xml}</vertices>'
        f'<triangles>{triangle_xml}</triangles></mesh></object>'
    )


def model_xml(objects=None, items='<item objectid="1"/>', unit="millimeter"):
    if objects is None:
        objects = mesh_object()
    return (
        f'<model xmlns="{CORE_NS}" xmlns:p="{PRODUCTION_NS}" unit="{unit}">'
        f'<resources>{objects}</resources><build>{items}</build></model>'
    )


def write_archive(path: Path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, value in entries:
            archive.writestr(name, value)
    return path


def write_large_torus(path: Path, rings=512, segments=1024) -> dict:
    """Write real geometry with bounded generation memory and predictable bounds."""
    if rings < 4 or segments < 4 or rings % 4 or segments % 4:
        raise ValueError("Use multiples of four so all coordinate extrema occur at vertices.")
    started = time.perf_counter()
    major_radius, minor_radius = 72.0, 24.0
    radii = [major_radius + minor_radius * math.cos(2 * math.pi * j / segments) for j in range(segments)]
    heights = [minor_radius * math.sin(2 * math.pi * j / segments) for j in range(segments)]
    model_name = "3D/3dmodel.model"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
            '</Types>',
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rel0" Target="/3D/3dmodel.model" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
            '</Relationships>',
        )
        with archive.open(model_name, "w", force_zip64=True) as destination:
            destination.write(
                ('<?xml version="1.0" encoding="UTF-8"?>\n'
                 f'<model xmlns="{CORE_NS}" unit="millimeter">\n'
                 '<resources><object id="1" type="model"><mesh><vertices>\n').encode("ascii")
            )
            for ring in range(rings):
                angle = 2 * math.pi * ring / rings
                cosine, sine = math.cos(angle), math.sin(angle)
                lines = [
                    f' <vertex x="{radius * cosine:.9f}" y="{radius * sine:.9f}" z="{z:.9f}"/>\n'
                    for radius, z in zip(radii, heights)
                ]
                destination.write("".join(lines).encode("ascii"))
            destination.write(b"</vertices><triangles>\n")
            for ring in range(rings):
                base = ring * segments
                next_base = ((ring + 1) % rings) * segments
                lines = []
                for segment in range(segments):
                    following = (segment + 1) % segments
                    a, b = base + segment, next_base + segment
                    c, d = next_base + following, base + following
                    lines.append(f' <triangle v1="{a}" v2="{b}" v3="{c}"/>\n')
                    lines.append(f' <triangle v1="{a}" v2="{c}" v3="{d}"/>\n')
                destination.write("".join(lines).encode("ascii"))
            destination.write(
                b'</triangles></mesh></object></resources>\n'
                b'<build><item objectid="1"/></build></model>\n'
            )
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(model_name)
        expanded, compressed = info.file_size, info.compress_size
    return {
        "archive_bytes": path.stat().st_size,
        "model_xml_bytes": expanded,
        "model_compressed_bytes": compressed,
        "compression_ratio": round(expanded / compressed, 3),
        "vertex_count": rings * segments,
        "triangle_count": 2 * rings * segments,
        "fixture_generation_seconds": round(time.perf_counter() - started, 3),
    }


_BENCHMARK_WORKER = r'''
import json
from pathlib import Path
import resource
import sys
import time
from app.mesh import inspect_3mf

scale = 1 if sys.platform == "darwin" else 1024
baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
started = time.perf_counter()
result = inspect_3mf(Path(sys.argv[1]))
elapsed = time.perf_counter() - started
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
print(json.dumps({
    "result": result,
    "parse_seconds": round(elapsed, 3),
    "baseline_peak_rss_bytes": int(baseline),
    "peak_rss_bytes": int(peak),
    "python": sys.version.split()[0],
}))
'''


def benchmark_inspection(path: Path) -> dict:
    """Measure a fresh parser process; fixture creation cannot inflate its RSS."""
    process = subprocess.run(
        [sys.executable, "-c", _BENCHMARK_WORKER, str(path.resolve())],
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if process.returncode:
        raise AssertionError(
            f"Standalone parser exited {process.returncode}:\n{process.stderr[-6000:]}"
        )
    return json.loads(process.stdout)


class Large3MFRegressionTests(unittest.TestCase):
    def test_real_mesh_over_old_64_mib_xml_limit_and_bounded_peak_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large_torus.3mf"
            fixture = write_large_torus(path)
            self.assertGreater(fixture["model_xml_bytes"], 64 * 1024**2)
            self.assertLess(fixture["compression_ratio"], 500)
            measured = benchmark_inspection(path)
        self.assertEqual(measured["result"]["triangle_count"], fixture["triangle_count"])
        self.assertEqual(measured["result"]["dimensions"], {"x": 192.0, "y": 192.0, "z": 48.0})
        self.assertTrue(measured["result"]["fit_p2s"])
        self.assertEqual(measured["result"]["units"], "mm")
        # This single 524k-vertex file should comfortably fit inside the app's
        # 1.5 GiB container with room for the server and concurrent lightweight
        # requests.  The guard catches a return to a retained XML element tree.
        self.assertLess(measured["peak_rss_bytes"], 512 * 1024**2)
        print("\nLARGE_3MF_BENCHMARK " + json.dumps(fixture | measured, sort_keys=True), flush=True)


class Streamed3MFValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "model.3mf"

    def tearDown(self):
        self.temporary.cleanup()

    def inspect_xml(self, xml):
        write_archive(self.path, [("3D/3dmodel.model", xml)])
        return mesh.inspect_3mf(self.path)

    def test_cross_document_components_keep_real_vertex_bounds_and_units(self):
        # The shear x'=x+y must transform actual tetrahedron vertices: its
        # maximum x is 3, whereas transforming only its bounding box gives 5.
        # With child translation 5 and build translation 10, the maximum is 18 cm.
        assembly = (
            '<object id="1" type="model"><components>'
            '<component objectid="7" p:path="Objects/shape.model"/>'
            '</components></object>'
            '<object id="2" type="model"><components>'
            '<component objectid="7" p:path="/3D/Objects/shape.model" '
            'transform="1 0 0 0 1 0 0 0 1 5 0 0"/>'
            '</components></object>'
        )
        build = (
            '<item objectid="1"/>'
            '<item objectid="2" transform="1 0 0 1 1 0 0 0 1 10 0 0"/>'
            '<item objectid="2" printable="false" transform="100 0 0 0 100 0 0 0 100 0 0 0"/>'
        )
        write_archive(self.path, [
            ("3D/3dmodel.model", model_xml(assembly, build, "centimeter")),
            ("3D/Objects/shape.model", model_xml(mesh_object("7"), "", "centimeter")),
        ])
        result = mesh.inspect_3mf(self.path)
        self.assertEqual(result["triangle_count"], 8)
        self.assertEqual(result["dimensions"], {"x": 180.0, "y": 30.0, "z": 40.0})

    def test_cross_document_mixed_units_require_reexport_but_unused_documents_do_not(self):
        assembly = (
            '<object id="1" type="model"><components>'
            '<component objectid="7" p:path="Objects/shape.model"/>'
            '</components></object>'
        )
        child = ("3D/Objects/shape.model", model_xml(mesh_object("7"), "", "centimeter"))
        write_archive(self.path, [
            ("3D/3dmodel.model", model_xml(assembly)),
            child,
        ])
        with self.assertRaisesRegex(mesh.ModelError, "[Rr]e-export"):
            mesh.inspect_3mf(self.path)
        write_archive(self.path, [
            ("3D/3dmodel.model", model_xml()),
            child,
        ])
        result = mesh.inspect_3mf(self.path)
        self.assertEqual(result["triangle_count"], 4)
        self.assertEqual(result["dimensions"], {"x": 2.0, "y": 3.0, "z": 4.0})

    def test_invalid_triangle_indices_remain_validation_errors(self):
        for invalid_triangle in ((-1, 1, 2), (0, 1, 4), ("0.5", 1, 2), (0, 1, 2**80)):
            with self.subTest(triangle=invalid_triangle):
                with self.assertRaises(mesh.ModelError):
                    self.inspect_xml(model_xml(mesh_object(triangles=[invalid_triangle])))

    def test_nonfinite_vertex_coordinates_remain_validation_errors(self):
        for value in ("nan", "inf", "-inf", "1e400"):
            with self.subTest(value=value):
                vertices = [(value, 0, 0), *TETRA_VERTICES[1:]]
                with self.assertRaises(mesh.ModelError):
                    self.inspect_xml(model_xml(mesh_object(vertices=vertices)))

    def test_truncated_or_misnested_xml_is_rejected(self):
        xml = model_xml()
        for malformed in (xml[:-8], xml.replace("</vertices>", "</triangles>")):
            with self.subTest(xml=malformed[-80:]):
                with self.assertRaises(mesh.ModelError):
                    self.inspect_xml(malformed)

    def test_streaming_parser_rejects_dtd_even_without_entity_expansion(self):
        for declaration in (
            '<!DOCTYPE model>',
            '<!DOCTYPE model [<!ENTITY coordinate "0">]>',
        ):
            with self.subTest(declaration=declaration):
                with self.assertRaises(mesh.ModelError):
                    self.inspect_xml(declaration + model_xml())

    def test_xml_size_and_document_count_caps_are_enforced_before_large_allocation(self):
        xml = model_xml()
        write_archive(self.path, [("3D/3dmodel.model", xml)])
        with patch.object(mesh, "MAX_3MF_MODEL_BYTES", len(xml.encode()) - 1):
            with self.assertRaises(mesh.ModelError):
                mesh.inspect_3mf(self.path)
        write_archive(self.path, [
            ("3D/3dmodel.model", xml),
            ("3D/Objects/extra.model", model_xml()),
        ])
        with patch.object(mesh, "MAX_3MF_MODEL_DOCUMENTS", 1):
            with self.assertRaises(mesh.ModelError):
                mesh.inspect_3mf(self.path)

    def test_resource_caps_apply_to_sum_across_objects(self):
        xml = model_xml(mesh_object("1") + mesh_object("2"), '<item objectid="1"/><item objectid="2"/>')
        write_archive(self.path, [("3D/3dmodel.model", xml)])
        for constant, cap in (
            ("MAX_3MF_VERTICES", 7),
            ("MAX_3MF_TRIANGLES", 7),
            ("MAX_3MF_OBJECTS", 1),
            ("MAX_3MF_BUILD_ITEMS", 1),
        ):
            with self.subTest(constant=constant), patch.object(mesh, constant, cap):
                with self.assertRaises(mesh.ModelError):
                    mesh.inspect_3mf(self.path)

    def test_vertex_and_triangle_caps_apply_to_sum_across_documents(self):
        # Each document is below the patched limit by itself.  The package as
        # a whole exceeds it, including resources that are not instantiated.
        write_archive(self.path, [
            ("3D/3dmodel.model", model_xml()),
            ("3D/Objects/extra.model", model_xml(mesh_object("2"), "")),
        ])
        for constant in ("MAX_3MF_VERTICES", "MAX_3MF_TRIANGLES"):
            with self.subTest(constant=constant), patch.object(mesh, constant, 7):
                with self.assertRaises(mesh.ModelError):
                    mesh.inspect_3mf(self.path)

    def test_instancing_cannot_bypass_assembled_triangle_or_vertex_caps(self):
        write_archive(self.path, [("3D/3dmodel.model", model_xml(items='<item objectid="1"/><item objectid="1"/>'))])
        for constant in ("MAX_3MF_TRIANGLES", "MAX_3MF_ASSEMBLED_VERTICES"):
            with self.subTest(constant=constant), patch.object(mesh, constant, 7):
                with self.assertRaises(mesh.ModelError):
                    mesh.inspect_3mf(self.path)

    def test_component_count_depth_and_visit_caps(self):
        objects = (
            mesh_object("1")
            + '<object id="2" type="model"><components><component objectid="1"/></components></object>'
            + '<object id="3" type="model"><components><component objectid="2"/></components></object>'
        )
        write_archive(self.path, [("3D/3dmodel.model", model_xml(objects, '<item objectid="3"/>'))])
        for constant, cap in (
            ("MAX_3MF_COMPONENTS", 1),
            ("MAX_3MF_COMPONENT_DEPTH", 1),
            ("MAX_3MF_VISITS", 2),
        ):
            with self.subTest(constant=constant), patch.object(mesh, constant, cap):
                with self.assertRaises(mesh.ModelError):
                    mesh.inspect_3mf(self.path)

    def test_xml_structure_budgets_apply_without_large_fixtures(self):
        write_archive(self.path, [("3D/3dmodel.model", model_xml())])
        # This is valid geometry before lowering each individual resource
        # budget. The cases therefore cannot pass merely because the XML is
        # malformed, the object is empty, or its build reference is missing.
        self.assertEqual(mesh.inspect_3mf(self.path)["triangle_count"], 4)
        for constant, cap in (
            ("MAX_3MF_XML_DEPTH", 5),
            ("MAX_3MF_XML_ATTRIBUTES", 2),
            ("MAX_3MF_XML_ATTRIBUTE_CHARS", 9),
            ("MAX_3MF_XML_NAME_CHARS", 4),
            ("MAX_3MF_XML_NAMES", 6),
            ("MAX_3MF_XML_NAMESPACES", 1),
            ("MAX_3MF_XML_ELEMENTS", 10),
        ):
            with self.subTest(constant=constant), patch.object(mesh, constant, cap):
                with self.assertRaises(mesh.ModelError):
                    mesh.inspect_3mf(self.path)

    def test_stream_boundary_splits_do_not_change_valid_geometry(self):
        write_archive(self.path, [("3D/3dmodel.model", model_xml())])
        with patch.object(mesh, "XML_READ_BYTES", 17):
            result = mesh.inspect_3mf(self.path)
        self.assertEqual(result["triangle_count"], 4)
        self.assertEqual(result["dimensions"], {"x": 2.0, "y": 3.0, "z": 4.0})

    def test_pending_xml_attributes_and_comments_cannot_grow_without_bound(self):
        for extra in (
            '<metadata name="example" value="' + "a" * 1024 + '"/>',
            "<!--" + "a" * 1024 + "-->",
        ):
            with self.subTest(token=extra[:30]):
                xml = model_xml().replace("<resources>", extra + "<resources>")
                write_archive(self.path, [("3D/3dmodel.model", xml)])
                self.assertEqual(mesh.inspect_3mf(self.path)["triangle_count"], 4)
                with patch.object(mesh, "XML_READ_BYTES", 64), patch.object(mesh, "MAX_3MF_XML_TOKEN_BYTES", 256):
                    with self.assertRaises(mesh.ModelError):
                        mesh.inspect_3mf(self.path)


if __name__ == "__main__":
    if sys.argv[1:] == ["--benchmark"]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large_torus.3mf"
            fixture = write_large_torus(path)
            measured = benchmark_inspection(path)
        print(json.dumps(fixture | measured, indent=2, sort_keys=True))
    else:
        unittest.main()
