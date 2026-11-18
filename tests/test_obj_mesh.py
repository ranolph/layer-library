"""Regression coverage for OBJ geometry, triangulation and resource budgets."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import obj_mesh
from app.mesh import ModelError


TETRA = """# A tetrahedron in millimetres
o tetra
v 0 0 0
v 2 0 0
v 0 3 0
v 0 0 4
f 1 3 2
f 1 2 4
f 1 4 3
f 2 3 4
"""


class ObjGeometryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "mesh.obj"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        if isinstance(value, str):
            value = value.encode("utf-8")
        self.path.write_bytes(value)
        return self.path

    def test_tetra_metadata_and_bounds(self):
        before = self.write(TETRA).read_bytes()
        info = obj_mesh.inspect_obj(self.path, include_bounds=True)
        self.assertEqual(info["dimensions"], {"x": 2.0, "y": 3.0, "z": 4.0})
        self.assertEqual(info["triangle_count"], 4)
        self.assertTrue(info["fit_p2s"])
        self.assertEqual(info["units"], "mm")
        self.assertEqual(info["bounds"], [[0., 0., 0.], [2., 3., 4.]])
        self.assertEqual(self.path.read_bytes(), before)

    def test_independent_chunks_and_unused_vertices(self):
        self.write(TETRA + "v 9000 9000 9000\n")
        chunks = list(obj_mesh.iter_obj_triangles(self.path, chunk_size=1))
        self.assertEqual([chunk.shape for chunk in chunks], [(1, 3, 3)] * 4)
        self.assertEqual(obj_mesh.inspect_obj(self.path)["dimensions"]["x"], 2.)
        self.assertFalse(np.shares_memory(chunks[0], chunks[1]))
        np.testing.assert_array_equal(chunks[0][0], [[0, 0, 0], [0, 3, 0], [2, 0, 0]])

    def test_negative_indices_resolve_at_face_not_end(self):
        self.write("v 0 0 0\nv 2 0 0\nv 0 3 0\nf -3 -2 -1\nv 99 99 99\n")
        self.assertEqual(obj_mesh.inspect_obj(self.path)["dimensions"], {"x": 2., "y": 3., "z": 0.})

    def test_all_common_face_reference_styles(self):
        self.write("v 0 0 0\nv 2 0 0\nv 0 3 0\nvt 0\nvt 1 0\nvt 0 1 0\nvn 0 0 1\n"
                   "f 1/1 2/2 3/3\nf 1//1 2//1 3//1\nf -3/-3/-1 -2/-2/-1 -1/-1/-1\n")
        self.assertEqual(obj_mesh.inspect_obj(self.path)["triangle_count"], 3)

    def test_colour_weight_and_metadata_are_inert(self):
        self.write("mtllib ../../secret.mtl\nusemtl untrusted\ncall /etc/passwd\ncsh ignored\n"
                   "o modèle\ng one two\ns 1\nv 0 0 0 2\nv 2 0 0 1 0 0\n"
                   "v 0 3 0 1 0 0 1\nv 0 0 4 1 .2 .3 .4 1\nf 1 2 3\nf 1 3 4\n")
        original_open, opened = Path.open, []
        def checked_open(path, *args, **kwargs):
            opened.append(path)
            return original_open(path, *args, **kwargs)
        with patch.object(Path, "open", new=checked_open):
            info = obj_mesh.inspect_obj(self.path)
        self.assertEqual(info["dimensions"], {"x": 2., "y": 3., "z": 4.})
        self.assertEqual(opened, [self.path])

    def test_bom_comments_windows_lines_and_continuation(self):
        self.write(b"\xef\xbb\xbfv 0 0 0\r\nv 2 0 0 # point\r\nv 0 3 0\r\nf 1 \\\r\n 2 3\r\n")
        self.assertEqual(obj_mesh.inspect_obj(self.path)["triangle_count"], 1)

    def test_concave_polygon_area_both_windings_and_axes(self):
        # L-shape: a naive fan from its inner corner adds overlapping triangles.
        polygon = [(1, 1), (1, 3), (0, 3), (0, 0), (3, 0), (3, 1)]
        for reverse in (False, True):
            for axis in (0, 1, 2):
                points = [list(point) for point in (polygon[::-1] if reverse else polygon)]
                for point in points:
                    point.insert(axis, 7)
                value = "".join("v " + " ".join(map(str, point)) + "\n" for point in points)
                self.write(value + "f 1 2 3 4 5 6\n")
                triangles = np.concatenate(list(obj_mesh.iter_obj_triangles(self.path)))
                areas = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                                triangles[:, 2] - triangles[:, 0]), axis=1) * .5
                self.assertEqual(len(triangles), 4)
                self.assertAlmostEqual(float(areas.sum()), 5.)
                normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
                self.assertTrue(np.all(normals[:, axis] > 0) or np.all(normals[:, axis] < 0))

    def test_closed_face_collinear_corner_and_duplicates(self):
        self.write("v 0 0 0\nv 1 0 0\nv 2 0 0\nv 2 2 0\nv 0 2 0\nf 1 2 2 3 4 5 1\n")
        triangles = np.concatenate(list(obj_mesh.iter_obj_triangles(self.path)))
        self.assertEqual(len(triangles), 2)
        area = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 2].sum() / 2
        self.assertAlmostEqual(float(area), 4.)

    def test_quad_and_slight_nonplanarity(self):
        self.write("v 0 0 0\nv 2 0 0\nv 2 2 .01\nv 0 2 0\nf 1 2 3 4\n")
        self.assertEqual(obj_mesh.inspect_obj(self.path)["triangle_count"], 2)

    def test_self_intersecting_polygons_rejected(self):
        self.write("v 0 0 0\nv 2 2 0\nv 0 2 0\nv 2 0 0\nf 1 2 3 4\n")
        with self.assertRaisesRegex(ModelError, "intersects"):
            obj_mesh.inspect_obj(self.path)

    def test_invalid_coordinate_and_attribute_values(self):
        for value in ("v nan 0 0", "v 0 inf 0", "v 1e309 0 0", "v 0 x 0", "vn nan 0 1",
                      "vt .5 -inf", "v 0 0 0 nan", "v 0 0 0 1 2", "v 1e13 0 0"):
            self.write(TETRA + value + "\n")
            with self.subTest(value=value), self.assertRaises(ModelError):
                obj_mesh.inspect_obj(self.path)

    def test_invalid_face_references(self):
        header = "v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvn 0 0 1\n"
        for face in ("0 2 3", "1 2 4", "-4 -2 -1", "1 2", "1/ 2 3", "1// 2 3",
                     "1/2 2 3", "1//2 2 3", "1/1/1/1 2 3", "1 2 a", "/1 2 3",
                     "1/0 2 3", "1//0 2 3"):
            self.write(header + "f " + face + "\n")
            with self.subTest(face=face), self.assertRaises(ModelError):
                obj_mesh.inspect_obj(self.path)

    def test_empty_point_cloud_and_freeform_only_rejected(self):
        for data in ("", "v 0 0 0\nv 1 0 0\np 1 2\n", "v 0 0 0\ncurv 0 1 1\nend\n"):
            self.write(data)
            with self.subTest(data=data), self.assertRaisesRegex(ModelError, "faces|polygon"):
                obj_mesh.inspect_obj(self.path)

    def test_no_forward_references(self):
        self.write("f 1 2 3\nv 0 0 0\nv 1 0 0\nv 0 1 0\n")
        with self.assertRaisesRegex(ModelError, "define it before"):
            obj_mesh.inspect_obj(self.path)

    def test_trailing_continuation_and_binary_rejected(self):
        for data in (TETRA + "f 1 2 \\\n", TETRA + "\x00"):
            self.write(data)
            with self.assertRaises(ModelError):
                obj_mesh.inspect_obj(self.path)

    def test_vertex_triangle_and_face_budgets(self):
        self.write(TETRA)
        for constant, value in (("MAX_OBJ_VERTICES", 3), ("MAX_OBJ_TRIANGLES", 3),
                                ("MAX_OBJ_FACE_VERTICES", 2), ("MAX_OBJ_BYTES", 20)):
            with self.subTest(constant=constant), patch.object(obj_mesh, constant, value), self.assertRaises(ModelError):
                obj_mesh.inspect_obj(self.path)

    def test_line_budgets(self):
        self.write(TETRA)
        for constant, value in (("MAX_OBJ_PHYSICAL_LINE", 10), ("MAX_OBJ_LOGICAL_LINE", 5),
                                ("MAX_OBJ_LINES", 2)):
            with self.subTest(constant=constant), patch.object(obj_mesh, constant, value), self.assertRaises(ModelError):
                obj_mesh.inspect_obj(self.path)

    def test_attribute_and_triangulation_budgets(self):
        self.write(TETRA + "vt 0\nvt 1\n")
        with patch.object(obj_mesh, "MAX_OBJ_ATTRIBUTE_VERTICES", 1), self.assertRaisesRegex(ModelError, "texture"):
            obj_mesh.inspect_obj(self.path)
        self.write("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3 4\n")
        with patch.object(obj_mesh, "MAX_OBJ_TRIANGULATION_WORK", 1), self.assertRaisesRegex(ModelError, "complex"):
            obj_mesh.inspect_obj(self.path)

    def test_tail_validation_after_first_chunk(self):
        self.write(TETRA + "v nan 0 0\n")
        iterator = obj_mesh.iter_obj_triangles(self.path, chunk_size=1)
        self.assertEqual(next(iterator).shape, (1, 3, 3))
        with self.assertRaises(ModelError):
            list(iterator)

    def test_bad_chunk_size(self):
        self.write(TETRA)
        for size in (0, -1, True, 65537, .5):
            with self.subTest(size=size), self.assertRaises(ValueError):
                list(obj_mesh.iter_obj_triangles(self.path, size))

    def test_budget_callback_checks_large_prefix_before_faces(self):
        self.write("# prefix\n" * 5000 + TETRA)
        calls = []
        def check():
            calls.append(True)
            if len(calls) == 2:
                raise TimeoutError("test deadline")
        with self.assertRaisesRegex(TimeoutError, "deadline"):
            list(obj_mesh.iter_obj_triangles(self.path, check_budget=check))
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
