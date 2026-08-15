"""
Pixel diff tests (Part 12).

Images are generated at test time via core.png_codec.encode_png — no binary
fixtures. Covers the comparison core, region detection, node attribution,
and the CLI contract (single JSON line; clean error sentinel on failure).

Run:  python3 -m unittest tests.test_pixel_diff -v
"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.pixel_diff import (
    attribute_regions,
    compare_images,
    compare_png_files,
    detect_regions,
    main,
)
from core.png_codec import PngImage, encode_png


def _solid(width, height, rgb, rect=None):
    """RGB pixels: solid fill, optionally overwritten by rect (x, y, w, h, rgb)."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            if (rect is not None
                    and rect[0] <= x < rect[0] + rect[2]
                    and rect[1] <= y < rect[1] + rect[3]):
                pixels.extend(rect[4])
            else:
                pixels.extend(rgb)
    return PngImage(width=width, height=height, channels=3, pixels=bytes(pixels))


class TestCompareImages(unittest.TestCase):
    def test_identical_images(self):
        img = _solid(4, 4, (255, 255, 255))
        stats, mask = compare_images(img, img)
        self.assertTrue(stats.identical)
        self.assertEqual(stats.diff_pixel_count, 0)
        self.assertEqual(stats.similarity, 1.0)
        self.assertEqual(stats.mae, {"r": 0.0, "g": 0.0, "b": 0.0})
        self.assertEqual(sum(mask), 0)

    def test_sub_threshold_jitter_ignored(self):
        a = _solid(4, 4, (100, 100, 100))
        b = _solid(4, 4, (110, 105, 100))  # max delta 10 < default threshold 16
        stats, mask = compare_images(a, b)
        self.assertEqual(stats.diff_pixel_count, 0)
        self.assertEqual(sum(mask), 0)
        # MAE still measures the raw deltas
        self.assertAlmostEqual(stats.mae["r"], 10.0)
        self.assertAlmostEqual(stats.mae["g"], 5.0)

    def test_block_above_threshold(self):
        # 8x8 white vs a 2x2 red block → 4 of 64 pixels differ
        a = _solid(8, 8, (255, 255, 255))
        b = _solid(8, 8, (255, 255, 255), rect=(0, 0, 2, 2, (255, 0, 0)))
        stats, mask = compare_images(a, b)
        self.assertEqual(stats.diff_pixel_count, 4)
        self.assertAlmostEqual(stats.diff_ratio, 4 / 64)
        self.assertEqual(sum(mask), 4)
        self.assertFalse(stats.identical)

    def test_size_mismatch_raises_value_error(self):
        a = _solid(4, 4, (0, 0, 0))
        b = _solid(5, 4, (0, 0, 0))
        with self.assertRaises(ValueError):
            compare_images(a, b)

    def test_exact_threshold_boundary_not_counted(self):
        # Delta == threshold must NOT be flagged (only deltas ABOVE count).
        a = _solid(4, 4, (100, 100, 100))
        b = _solid(4, 4, (116, 100, 100))  # delta exactly 16 == default threshold
        stats, mask = compare_images(a, b)
        self.assertEqual(stats.diff_pixel_count, 0)
        self.assertEqual(sum(mask), 0)
        self.assertAlmostEqual(stats.mae["r"], 16.0)

    def test_rgb_vs_rgba_mixed_channels(self):
        # RGB vs RGBA with identical RGB (+ opaque alpha) → no diff;
        # alpha must not participate.
        a = _solid(2, 2, (10, 20, 30))
        rgba = bytearray()
        for _ in range(4):
            rgba.extend((10, 20, 30, 255))
        b = PngImage(width=2, height=2, channels=4, pixels=bytes(rgba))
        stats, mask = compare_images(a, b)
        self.assertTrue(stats.identical)
        self.assertEqual(stats.diff_pixel_count, 0)
        self.assertEqual(sum(mask), 0)


class TestComparePngFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name, image):
        path = self.dir / name
        path.write_bytes(encode_png(image))
        return path

    def test_success_result_shape(self):
        a = self._write("a.png", _solid(4, 4, (10, 20, 30)))
        b = self._write("b.png", _solid(4, 4, (10, 20, 30)))
        result = compare_png_files(a, b)
        self.assertTrue(result["ok"])
        for key in ("similarity", "diffPixelCount", "diffPercentage",
                    "totalPixels", "width", "height", "identical",
                    "meanAbsoluteError"):
            self.assertIn(key, result)
        self.assertEqual(result["totalPixels"], 16)
        self.assertEqual(result["width"], 4)
        self.assertEqual(result["height"], 4)

    def test_size_mismatch_is_clean_error(self):
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        b = self._write("b.png", _solid(6, 4, (0, 0, 0)))
        result = compare_png_files(a, b)
        self.assertFalse(result["ok"])
        self.assertIn("size mismatch", result["error"])

    def test_corrupt_file_is_clean_error(self):
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        bad = self.dir / "bad.png"
        bad.write_bytes(b"this is not a png")
        result = compare_png_files(a, bad)
        self.assertFalse(result["ok"])
        self.assertIn("not a PNG", result["error"])


class TestDetectRegions(unittest.TestCase):
    def test_contiguous_block_detected(self):
        # 8x8 mask with one 4x4 block (area 16) and 2 scattered pixels
        mask = bytearray(64)
        for y in range(4):
            for x in range(4):
                mask[y * 8 + x] = 1
        mask[8 * 7 + 7] = 1  # scattered
        mask[8 * 6 + 1] = 1  # scattered
        regions = detect_regions(mask, 8, 8, min_region_area=8)
        self.assertEqual(len(regions), 1)
        region = regions[0]
        self.assertEqual(region["x"], 0)
        self.assertEqual(region["y"], 0)
        self.assertEqual(region["width"], 4)
        self.assertEqual(region["height"], 4)
        self.assertEqual(region["area"], 16)

    def test_small_regions_filtered_out(self):
        mask = bytearray(64)
        mask[0] = 1
        mask[1] = 1
        regions = detect_regions(mask, 8, 8, min_region_area=8)
        self.assertEqual(regions, [])

    def test_diagonal_pixels_are_separate_regions(self):
        # 4-connectivity only: two diagonally adjacent diff pixels are NOT
        # one region.
        mask = bytearray(64)
        mask[0 * 8 + 0] = 1  # (0, 0)
        mask[1 * 8 + 1] = 1  # (1, 1) — diagonal
        regions = detect_regions(mask, 8, 8, min_region_area=1)
        self.assertEqual(len(regions), 2)


class TestAttributeRegions(unittest.TestCase):
    RENDER_META = {
        "n1": {"x": 0, "y": 0, "width": 100, "height": 100},
        "n2": {"x": 100, "y": 0, "width": 50, "height": 50},
    }

    def test_largest_overlap_wins(self):
        region = {"x": 10, "y": 10, "width": 20, "height": 20, "area": 400}
        attributed = attribute_regions([region], self.RENDER_META, "root")
        self.assertEqual(attributed, [(region, "n1")])

    def test_no_overlap_falls_back_to_root(self):
        region = {"x": 300, "y": 300, "width": 10, "height": 10, "area": 100}
        attributed = attribute_regions([region], self.RENDER_META, "root")
        self.assertEqual(attributed, [(region, "root")])

    def test_tie_prefers_more_specific_node(self):
        # Region fully inside both a parent and a child → equal overlap;
        # the smaller (more specific) node must win.
        region = {"x": 0, "y": 0, "width": 10, "height": 10, "area": 100}
        meta = {
            "frame-root": {"x": 0, "y": 0, "width": 800, "height": 600},
            "n1": {"x": 0, "y": 0, "width": 100, "height": 100},
        }
        attributed = attribute_regions([region], meta, "frame-root")
        self.assertEqual(attributed, [(region, "n1")])

    def test_empty_render_meta_falls_back_to_root(self):
        region = {"x": 5, "y": 5, "width": 10, "height": 10, "area": 100}
        attributed = attribute_regions([region], {}, "root")
        self.assertEqual(attributed, [(region, "root")])

    def test_equal_area_tie_is_deterministic(self):
        # Two nodes with identical bboxes → equal overlap AND equal area.
        # The winner must be stable: first node in meta order.
        region = {"x": 0, "y": 0, "width": 10, "height": 10, "area": 100}
        meta = {
            "n1": {"x": 0, "y": 0, "width": 50, "height": 50},
            "n2": {"x": 0, "y": 0, "width": 50, "height": 50},
        }
        first = attribute_regions([region], meta, "root")
        second = attribute_regions([region], meta, "root")
        self.assertEqual(first, second)
        self.assertEqual(first, [(region, "n1")])


class TestCli(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name, image):
        path = self.dir / name
        path.write_bytes(encode_png(image))
        return path

    def _run_main(self, argv):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(argv)
        return code, stdout.getvalue().strip()

    def test_cli_success_json_line(self):
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        b = self._write("b.png", _solid(4, 4, (0, 0, 0)))
        code, out = self._run_main(["--a", str(a), "--b", str(b)])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["identical"], True)
        self.assertEqual(payload["similarity"], 1.0)
        self.assertEqual(payload["totalPixels"], 16)

    def test_cli_error_exits_one_with_sentinel(self):
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        code, out = self._run_main(["--a", str(a), "--b", str(self.dir / "missing.png")])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertIn("error", payload)

    def test_cli_missing_required_arg_emits_json_error(self):
        # Missing --b must NOT raise SystemExit(2) with empty stdout —
        # the one-JSON-line contract holds for bad invocations too.
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        code, out = self._run_main(["--a", str(a)])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertIn("invalid arguments", payload["error"])

    def test_cli_non_numeric_threshold_emits_json_error(self):
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        b = self._write("b.png", _solid(4, 4, (0, 0, 0)))
        code, out = self._run_main(
            ["--a", str(a), "--b", str(b), "--threshold", "abc"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertIn("invalid arguments", payload["error"])

    def test_cli_negative_threshold_rejected(self):
        # threshold < 0 would invert semantics (identical pixels flagged);
        # reject it explicitly.
        a = self._write("a.png", _solid(4, 4, (0, 0, 0)))
        b = self._write("b.png", _solid(4, 4, (0, 0, 0)))
        code, out = self._run_main(
            ["--a", str(a), "--b", str(b), "--threshold", "-1"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["error"], "threshold must be >= 0")

    def test_cli_missing_required_arg_via_subprocess(self):
        a = self._write("a.png", _solid(2, 2, (1, 2, 3)))
        proc = subprocess.run(
            [sys.executable, "-m", "core.pixel_diff", "--a", str(a)],
            cwd=str(plugin_root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertIn("invalid arguments", payload["error"])

    def test_module_execution_via_subprocess(self):
        a = self._write("a.png", _solid(2, 2, (1, 2, 3)))
        b = self._write("b.png", _solid(2, 2, (1, 2, 3)))
        proc = subprocess.run(
            [sys.executable, "-m", "core.pixel_diff", "--a", str(a), "--b", str(b)],
            cwd=str(plugin_root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["identical"])


if __name__ == "__main__":
    unittest.main()
