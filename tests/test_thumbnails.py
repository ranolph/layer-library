"""Regression coverage for real server-rendered model preview images."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image, ImageDraw

from app.mesh import ModelError
from app import thumbnails


VERTICES = np.asarray([(0, 0, 0), (30, 0, 0), (0, 20, 0), (0, 0, 35)], dtype=float)
FACES = np.asarray([(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)])


def stl_bytes(triangles, header=b"test geometry"):
    output = bytearray(header.ljust(80, b" ")[:80])
    output.extend(struct.pack("<I", len(triangles)))
    for triangle in triangles:
        output.extend(struct.pack("<12fH", 0, 0, 0, *np.asarray(triangle).ravel(), 0))
    return bytes(output)


def png_bytes(color="white", *, picture=True, transparent=False):
    image = Image.new("RGBA" if transparent else "RGB", (120, 100), (0, 0, 0, 0) if transparent else color)
    if picture:
        ImageDraw.Draw(image).polygon([(12, 88), (100, 72), (60, 8)], fill=(0, 0, 0, 255) if transparent else "navy")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.destination = self.root / "previews" / "model.png"

    def tearDown(self):
        self.temp.cleanup()

    def model(self, content, name="model.stl"):
        path = self.root / name
        path.write_bytes(content)
        return path

    def assert_picture(self, path):
        with Image.open(path) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (640, 480))
            pixels = np.asarray(image.convert("RGB"))
        changed = np.any(np.abs(pixels.astype(int) - thumbnails.BACKGROUND) > 12, axis=2)
        self.assertGreater(int(changed.sum()), 500)
        self.assertLess(int(changed.sum()), pixels.shape[0] * pixels.shape[1] * .9)
        return pixels

    def test_binary_stl_real_image_source_unchanged(self):
        path = self.model(stl_bytes(VERTICES[FACES], header=b"solid also a binary header"))
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = thumbnails.render_thumbnail(path, "stl", self.destination)
        self.assertEqual(result["source"], "mesh")
        self.assertEqual(result["triangle_count"], 4)
        self.assertFalse(result["sampled"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assert_picture(self.destination)

    def test_triangle_order_does_not_change_occlusion(self):
        path = self.model(stl_bytes(VERTICES[FACES]))
        thumbnails.render_thumbnail(path, "stl", self.destination)
        before = self.assert_picture(self.destination)
        path.write_bytes(stl_bytes(VERTICES[FACES][::-1]))
        thumbnails.render_thumbnail(path, "stl", self.destination)
        np.testing.assert_array_equal(before, self.assert_picture(self.destination))

    def test_far_from_origin_is_fitted_without_blank_image(self):
        path = self.model(stl_bytes(VERTICES[FACES]))
        thumbnails.render_thumbnail(path, "stl", self.destination)
        before = self.assert_picture(self.destination)
        path.write_bytes(stl_bytes((VERTICES + (40000, -20000, 30000))[FACES]))
        thumbnails.render_thumbnail(path, "stl", self.destination)
        np.testing.assert_array_equal(before, self.assert_picture(self.destination))

    def test_ascii_stl_flat_triangle(self):
        path = self.model(b"""solid flat
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 30 0 0
vertex 0 25 0
endloop
endfacet
endsolid flat
""")
        thumbnails.render_thumbnail(path, "stl", self.destination)
        self.assert_picture(self.destination)

    def test_disconnected_geometry_is_all_in_frame(self):
        geometry = np.concatenate((VERTICES[FACES], (VERTICES + (80, 0, 0))[FACES]))
        path = self.model(stl_bytes(geometry))
        report = thumbnails.render_thumbnail(path, "stl", self.destination)
        self.assertEqual(report["triangle_count"], 8)
        pixels = self.assert_picture(self.destination)
        active = np.any(np.abs(pixels.astype(int) - thumbnails.BACKGROUND) > 20, axis=2)
        self.assertGreater(int(active[:, :320].sum()), 500)
        self.assertGreater(int(active[:, 320:].sum()), 500)

    def test_empty_degenerate_and_bad_stl_do_not_replace_previous(self):
        self.destination.parent.mkdir()
        self.destination.write_bytes(b"old image")
        for data in (b"", b"solid bad\nvertex 0 0 0\nendsolid bad\n",
                     stl_bytes(np.zeros((1, 3, 3))),
                     stl_bytes(np.asarray([[(0, 0, 0), (1, 1, 1), (2, 2, 2)]])),
                     stl_bytes(np.asarray([[(0, 0, 0), (float("nan"), 0, 0), (0, 1, 0)]]))):
            with self.subTest(data=data[:20]):
                path = self.model(data)
                with self.assertRaises(ModelError):
                    thumbnails.render_thumbnail(path, "stl", self.destination)
                self.assertEqual(self.destination.read_bytes(), b"old image")

    def test_time_and_work_limits_preserve_old_image(self):
        path = self.model(stl_bytes(VERTICES[FACES]))
        self.destination.parent.mkdir()
        self.destination.write_bytes(b"existing")
        for name, value in (("MAX_SECONDS", 0), ("MAX_RASTER_SAMPLES", 1)):
            with patch.object(thumbnails, name, value), self.assertRaisesRegex(ModelError, "preview limit"):
                thumbnails.render_thumbnail(path, "stl", self.destination)
            self.assertEqual(self.destination.read_bytes(), b"existing")
        self.assertEqual(list(self.destination.parent.glob("*.tmp")), [])

    def test_never_writes_over_original_model(self):
        data = stl_bytes(VERTICES[FACES])
        path = self.model(data)
        with self.assertRaisesRegex(ModelError, "separate"):
            thumbnails.render_thumbnail(path, "stl", path)
        self.assertEqual(path.read_bytes(), data)

    def test_obj_polygon_geometry(self):
        path = self.model(b"v 0 0 0\nv 30 0 0\nv 30 20 0\nv 15 8 0\nv 0 20 0\nf 1 2 3 4 5\n", "concave.obj")
        result = thumbnails.render_thumbnail(path, "obj", self.destination)
        self.assertEqual(result["triangle_count"], 3)
        self.assert_picture(self.destination)

    def archive(self, entries):
        path = self.root / "model.3mf"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
        return path

    def test_3mf_embedded_image_and_relationship(self):
        relationship = b'<Relationships><Relationship Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail" Target="/Images/picture.png"/></Relationships>'
        path = self.archive([("_rels/.rels", relationship), ("Images/picture.png", png_bytes())])
        report = thumbnails.render_thumbnail(path, "3mf", self.destination)
        self.assertEqual(report["source"], "embedded")
        self.assert_picture(self.destination)

    def test_3mf_transparent_silhouette_is_a_usable_picture(self):
        path = self.archive([("Metadata/thumbnail.png", png_bytes(transparent=True))])
        thumbnails.render_thumbnail(path, "3mf", self.destination)
        self.assert_picture(self.destination)

    def test_3mf_no_picture_or_blank_picture_clear_error(self):
        for entries in ([("3D/model.model", b"<model/>")],
                        [("Metadata/thumbnail.png", png_bytes(picture=False))]):
            path = self.archive(entries)
            with self.assertRaisesRegex(ModelError, "no usable embedded"):
                thumbnails.render_thumbnail(path, "3mf", self.destination)
            self.assertFalse(self.destination.exists())

    def test_external_3mf_thumbnail_is_never_followed(self):
        relationship = b'<Relationships><Relationship Type="http://example/thumbnail" TargetMode="External" Target="http://192.168.1.1/secret.png"/></Relationships>'
        path = self.archive([("_rels/.rels", relationship)])
        with self.assertRaisesRegex(ModelError, "no usable embedded"):
            thumbnails.render_thumbnail(path, "3mf", self.destination)

    def test_broken_relationship_does_not_hide_safe_standard_picture(self):
        path = self.archive([("_rels/.rels", b"<broken"), ("Metadata/thumbnail.png", png_bytes())])
        thumbnails.render_thumbnail(path, "3mf", self.destination)
        self.assert_picture(self.destination)


if __name__ == "__main__":
    unittest.main()
