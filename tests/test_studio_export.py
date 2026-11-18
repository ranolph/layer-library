"""Complete geometric and package round trips for the Bambu Studio handoff."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import studio_export as exporter
from app.mesh import ModelError, inspect_3mf
from app.obj_mesh import iter_obj_triangles
from app.thumbnails import _stl_triangles


VERTICES = [(0., 0., 0.), (2., 0., 0.), (0., 3., 0.), (0., 0., 4.)]
FACES = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]


def binary_stl(vertices=VERTICES, faces=FACES):
    data = bytearray(b"Studio exporter tetra".ljust(80, b" "))
    data.extend(struct.pack("<I", len(faces)))
    for face in faces:
        data.extend(struct.pack("<12fH", 0, 0, 0, *(v for index in face for v in vertices[index]), 0))
    return bytes(data)


def ascii_stl(vertices=VERTICES, faces=FACES):
    lines = ["solid exact\n"]
    for face in faces:
        lines.append("facet normal 0 0 0\nouter loop\n")
        lines.extend("vertex " + " ".join(format(v, ".17g") for v in vertices[index]) + "\n" for index in face)
        lines.append("endloop\nendfacet\n")
    return "".join(lines) + "endsolid exact\n"


def obj_tetra(vertices=VERTICES):
    return "".join("v " + " ".join(format(v, ".17g") for v in vertex) + "\n" for vertex in vertices) + \
        "".join("f " + " ".join(str(index + 1) for index in face) + "\n" for face in FACES)


def decode(path):
    with zipfile.ZipFile(path) as package:
        if package.testzip() is not None:
            raise AssertionError("3MF ZIP CRC failure")
        root = ET.fromstring(package.read(exporter.MODEL_PART))
        ns = {"m": exporter.CORE_NS}
        vertices = np.asarray([[float(v.attrib[axis]) for axis in ("x", "y", "z")]
                               for v in root.findall("m:resources/m:object/m:mesh/m:vertices/m:vertex", ns)])
        faces = np.asarray([[int(f.attrib[axis]) for axis in ("v1", "v2", "v3")]
                            for f in root.findall("m:resources/m:object/m:mesh/m:triangles/m:triangle", ns)])
        return root, vertices, faces, vertices[faces]


class StudioExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mesh"
        self.destination = self.root / "exports" / "handoff.3mf"

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, content):
        self.source.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)

    def assert_clean(self):
        if self.destination.parent.exists():
            self.assertEqual(list(self.destination.parent.glob(".studio-export-*")), [])

    def roundtrip(self, kind):
        original = self.source.read_bytes()
        info = exporter.export_model_3mf(self.source, kind, self.destination)
        self.assertEqual(self.source.read_bytes(), original)
        root, vertices, faces, triangles = decode(self.destination)
        factory = _stl_triangles(self.source) if kind == "stl" else iter_obj_triangles(self.source)
        expected = np.concatenate(list(factory))
        np.testing.assert_array_equal(triangles, expected)
        inspected = inspect_3mf(self.destination)
        self.assertEqual(inspected["dimensions"], info["dimensions"])
        self.assertEqual(inspected["triangle_count"], len(expected))
        self.assertEqual(info["vertex_count"], len(vertices))
        self.assertEqual(info["triangle_count"], len(faces))
        self.assertEqual(info["units"], "mm")
        self.assertEqual(info["size_bytes"], self.destination.stat().st_size)
        self.assertEqual(root.attrib["unit"], "millimeter")
        self.assert_clean()
        return info, root, vertices, faces, triangles

    def test_binary_stl_roundtrip_welds_shared_vertices_and_preserves_winding(self):
        self.write(binary_stl())
        info, _, vertices, faces, _ = self.roundtrip("stl")
        self.assertEqual(info["vertex_count"], 4)
        edges = Counter(tuple(sorted(edge)) for a, b, c in faces for edge in ((a,b), (b,c), (c,a)))
        self.assertEqual(set(edges.values()), {2})
        directed = Counter(edge for a,b,c in faces for edge in ((a,b), (b,c), (c,a)))
        self.assertTrue(all(directed[(b,a)] == count for (a,b),count in directed.items()))

    def test_ascii_stl_float64_roundtrip_and_origin_are_exact(self):
        points = [(-12.123456789012345, 7.00000000000001, 3e-10),
                  (8.765432109876543, 7.00000000000001, 3e-10),
                  (-12.123456789012345, 11.00000000000001, 3e-10),
                  (-12.123456789012345, 7.00000000000001, 17.345678901234567)]
        self.write(ascii_stl(points))
        _, _, _, _, triangles = self.roundtrip("stl")
        self.assertEqual(float(triangles[:,:,0].min()), points[0][0])

    def test_concave_obj_negative_and_slash_refs_roundtrip(self):
        polygon = [(1, 1), (1, 3), (0, 3), (0, 0), (3, 0), (3, 1)]
        self.write("mtllib missing.mtl\n" + "".join(f"v {x} {y} -5\n" for x,y in polygon) +
                   "vt 0 0\nvn 0 0 1\nusemtl ignored\nf -6/1/1 -5/1/1 -4/1/1 -3/1/1 -2/1/1 -1/1/1\n")
        info, _, _, _, triangles = self.roundtrip("obj")
        self.assertEqual(info["triangle_count"], 4)
        self.assertEqual(info["vertex_count"], 6)
        area = np.linalg.norm(np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),axis=1).sum()/2
        self.assertEqual(float(area), 5.)

    def test_standard_package_relationships_content_types_and_no_external_parts(self):
        self.write(obj_tetra())
        info, root, _, _, _ = self.roundtrip("obj")
        with zipfile.ZipFile(self.destination) as package:
            self.assertEqual(set(package.namelist()), {"[Content_Types].xml", "_rels/.rels", exporter.MODEL_PART})
            relationships = ET.fromstring(package.read("_rels/.rels"))
            relation, = list(relationships)
            self.assertEqual(relation.attrib["Target"], "/" + exporter.MODEL_PART)
            self.assertEqual(relation.attrib["Type"], exporter.START_REL)
            self.assertNotIn("TargetMode", relation.attrib)
            content = ET.fromstring(package.read("[Content_Types].xml"))
            override = next(x for x in content if x.tag.endswith("Override"))
            self.assertEqual(override.attrib["ContentType"], exporter.MODEL_CONTENT_TYPE)
            self.assertEqual(info["model_xml_bytes"], package.getinfo(exporter.MODEL_PART).file_size)
        ns = {"m": exporter.CORE_NS}
        self.assertEqual(root.find("m:metadata",ns).text, "Layer Library")
        self.assertEqual(root.find("m:build/m:item",ns).attrib, {"objectid":"1"})

    def test_signed_zero_and_cross_chunk_duplicate_welding(self):
        self.write(obj_tetra([(-0.,0.,0.),(2.,-0.,0.),(0.,3.,-0.),(0.,0.,4.)]))
        with patch.object(exporter, "CHUNK_TRIANGLES", 1):
            info, _, _, _, _ = self.roundtrip("obj")
        self.assertEqual(info["vertex_count"], 4)

    def test_source_mesh_and_xml_limits_preserve_old_destination(self):
        self.write(binary_stl())
        self.destination.parent.mkdir()
        old = b"unchanged previous handoff"
        self.destination.write_bytes(old)
        for name, value in (("MAX_SOURCE_BYTES", 1), ("MAX_VERTICES", 3), ("MAX_TRIANGLES", 3),
                            ("MAX_XML_BYTES", 200), ("MAX_EXPANDED_BYTES", 100),
                            ("MAX_OUTPUT_BYTES", 10), ("MAX_TEMP_BYTES", 10)):
            with self.subTest(name=name), patch.object(exporter, name, value):
                with self.assertRaisesRegex(ModelError, "Download the original"):
                    exporter.export_model_3mf(self.source,"stl",self.destination)
                self.assertEqual(self.destination.read_bytes(),old)
                self.assertEqual(self.source.read_bytes(),binary_stl())
                self.assert_clean()

    def test_timeout_preserves_destination_and_cleans_workspace(self):
        self.write(binary_stl())
        self.destination.parent.mkdir()
        self.destination.write_bytes(b"old")
        with patch.object(exporter, "MAX_SECONDS", -1), self.assertRaisesRegex(ModelError,"time limit"):
            exporter.export_model_3mf(self.source,"stl",self.destination)
        self.assertEqual(self.destination.read_bytes(),b"old")
        self.assert_clean()

    def test_invalid_source_tail_is_not_silently_truncated(self):
        self.write(obj_tetra() + "v nan 0 0\n")
        with patch.object(exporter, "CHUNK_TRIANGLES", 1), self.assertRaisesRegex(ModelError,"non-finite"):
            exporter.export_model_3mf(self.source,"obj",self.destination)
        self.assertFalse(self.destination.exists())
        self.assert_clean()

    def test_empty_invalid_and_unknown_formats(self):
        for data,kind in ((b"","stl"),(b"not STL","stl"),(b"v 0 0 0\n","obj"),(binary_stl(),"3mf")):
            self.write(data)
            with self.subTest(kind=kind,data=data[:10]), self.assertRaises(ModelError):
                exporter.export_model_3mf(self.source,kind,self.destination)
            self.assertFalse(self.destination.exists())
            self.assert_clean()

    def test_source_destination_alias_rejected(self):
        self.write(binary_stl())
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ModelError,"separate"):
            exporter.export_model_3mf(self.source,"stl",self.source)
        alias = self.root/'alias.3mf'
        alias.symlink_to(self.source)
        with self.assertRaisesRegex(ModelError,"separate"):
            exporter.export_model_3mf(self.source,"stl",alias)
        self.assertEqual(self.source.read_bytes(),before)

    def test_source_change_during_conversion_fails_without_publishing(self):
        self.write(binary_stl())
        original_iterator = exporter._stl_triangles
        def changed_source(path,budget):
            yield from original_iterator(path,budget)
            path.write_bytes(binary_stl([(x+1,y,z) for x,y,z in VERTICES]))
        with patch.object(exporter,"_stl_triangles",changed_source), self.assertRaisesRegex(ModelError,"changed"):
            exporter.export_model_3mf(self.source,"stl",self.destination)
        self.assertFalse(self.destination.exists())
        self.assert_clean()

    def test_atomic_replace_failure_cleans_temporary_files_and_keeps_old_bytes(self):
        self.write(binary_stl())
        self.destination.parent.mkdir()
        self.destination.write_bytes(b"old")
        with patch.object(exporter.os,"replace",side_effect=OSError("disk error")), self.assertRaises(ModelError):
            exporter.export_model_3mf(self.source,"stl",self.destination)
        self.assertEqual(self.destination.read_bytes(),b"old")
        self.assert_clean()

    def test_concurrent_exports_have_isolated_workspaces_and_complete_outputs(self):
        self.write(binary_stl())
        other=self.root/'second.obj'
        other.write_text(obj_tetra([(x+50,y-20,z+100) for x,y,z in VERTICES]))
        before={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.source,other)}
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(exporter.export_model_3mf,self.source,"stl",self.destination)
            second=pool.submit(exporter.export_model_3mf,other,"obj",self.destination)
            self.assertEqual(first.result()["triangle_count"],4)
            self.assertEqual(second.result()["triangle_count"],4)
        _,_,_,actual=decode(self.destination)
        candidates=[np.concatenate(list(_stl_triangles(self.source))),np.concatenate(list(iter_obj_triangles(other)))]
        self.assertTrue(any(np.array_equal(actual,value) for value in candidates))
        for path,digest in before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),digest)
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
