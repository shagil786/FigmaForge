#!/usr/bin/env python3
"""
Tests for DiffEngine (Part 7).

Verify that DiffEngine correctly identifies geometry and style mismatches
between a predicted LayoutPlan and dummy render metadata.
"""
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.diff_engine import DiffEngine, DiffReport, RasterOptions
from core.png_codec import PngImage, encode_png


class _MockBox:
    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.width = w
        self.height = h


class _MockText:
    def __init__(self, font_size=None):
        self.font_size = font_size


class _MockNode:
    def __init__(self, nid, x=0, y=0, w=100, h=50, font_size=None):
        self.node_id = nid
        self.box = _MockBox(x, y, w, h)
        self.text = _MockText(font_size) if font_size else None

    def nodes(self):
        return [self]


class _MockPlan:
    """A plan with multiple nodes for testing."""
    def __init__(self, nodes):
        self._nodes = nodes

    def nodes(self):
        return self._nodes


class TestDiffEngineGeometryMismatch(unittest.TestCase):
    def test_width_mismatch_detected(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 105, "height": 50}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(len(report.mismatches), 1)
        self.assertEqual(report.mismatches[0]["type"], "geometry_mismatch")

    def test_exact_match_no_mismatches(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(len(report.mismatches), 0)
        self.assertEqual(report.similarity_score, 1.0)

    def test_within_tolerance_no_mismatch(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0.5, "y": 0.5, "width": 100.5, "height": 50.5}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(len(report.mismatches), 0)

    def test_missing_in_render(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(report.mismatches[0]["type"], "missing_in_render")


class TestDiffEngineStyleMismatch(unittest.TestCase):
    def test_typography_mismatch_detected(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50, font_size=16)])
        render_meta = {"n1": {"styles": {"fontSize": 20}}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        style_mismatches = [m for m in report.mismatches if m["type"] == "typography_mismatch"]
        self.assertEqual(len(style_mismatches), 1)

    def test_typography_within_tolerance(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50, font_size=16)])
        render_meta = {"n1": {"styles": {"fontSize": 16.5}}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        style_mismatches = [m for m in report.mismatches if m["type"] == "typography_mismatch"]
        self.assertEqual(len(style_mismatches), 0)


class TestDiffEngineScoreClamping(unittest.TestCase):
    def test_score_clamped_to_zero(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 999, "y": 999, "width": 999, "height": 999, "styles": {"fontSize": 999}}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertGreaterEqual(report.similarity_score, 0.0)

    def test_categories_are_per_category(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 999, "y": 0, "width": 100, "height": 50}}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertLess(report.categories["geometry"], 1.0)
        self.assertEqual(report.categories["style"], 1.0)

    def test_empty_plan_score_is_one(self):
        plan = _MockPlan([])
        render_meta = {}
        engine = DiffEngine()
        report = engine.diff(plan, render_meta)
        self.assertEqual(report.similarity_score, 1.0)


class TestDiffReportSerialization(unittest.TestCase):
    def test_to_dict(self):
        report = DiffReport(
            similarity_score=0.8,
            categories={"geometry": 0.9, "style": 0.7, "pixels": 1.0},
            mismatches=[{"node_id": "n1", "type": "geometry_mismatch"}],
        )
        d = report.to_dict()
        self.assertEqual(d["similarity_score"], 0.8)
        self.assertEqual(len(d["mismatches"]), 1)
        self.assertEqual(d["categories"]["geometry"], 0.9)


def _solid_png_bytes(width, height, rgb, rect=None):
    """RGB PNG bytes: solid fill, optionally overwritten by rect (x, y, w, h, rgb)."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            if (rect is not None
                    and rect[0] <= x < rect[0] + rect[2]
                    and rect[1] <= y < rect[1] + rect[3]):
                pixels.extend(rect[4])
            else:
                pixels.extend(rgb)
    return encode_png(PngImage(width=width, height=height, channels=3,
                               pixels=bytes(pixels)))


class _RasterTmp:
    """Tempdir helper for raster tests."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        return self

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def __exit__(self, *exc):
        self._tmp.cleanup()


class TestDiffEngineRaster(unittest.TestCase):
    def test_both_raster_args_omitted_is_legacy_behavior(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        report = DiffEngine().diff(plan, render_meta)
        self.assertEqual(report.similarity_score, 1.0)
        self.assertEqual(report.categories["pixels"], 1.0)
        self.assertIsNone(report.raster_stats)
        self.assertIsNone(report.to_dict()["raster_stats"])

    def test_only_one_raster_arg_still_structural_only(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", _solid_png_bytes(4, 4, (0, 0, 0)))
            report = DiffEngine().diff(plan, render_meta, render_screenshot=str(shot))
        self.assertIsNone(report.raster_stats)
        self.assertEqual(report.similarity_score, 1.0)

    def test_identical_raster_scores_one(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        png = _solid_png_bytes(8, 8, (255, 255, 255))
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", png)
            base = tmp.write("base.png", png)
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        self.assertEqual(report.categories["pixels"], 1.0)
        self.assertEqual(report.similarity_score, 1.0)
        self.assertEqual(report.raster_stats["diff_percentage"], 0.0)
        self.assertEqual(report.raster_stats["region_count"], 0)

    def test_noise_below_floor_keeps_pixels_at_one(self):
        # 100x100 with a 10x10 diff block → diffRatio exactly 0.01 == floor
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        shot_bytes = _solid_png_bytes(
            100, 100, (255, 255, 255), rect=(0, 0, 10, 10, (0, 0, 0))
        )
        base_bytes = _solid_png_bytes(100, 100, (255, 255, 255))
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", shot_bytes)
            base = tmp.write("base.png", base_bytes)
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        self.assertEqual(report.categories["pixels"], 1.0)
        # Part 13: at-or-below the noise floor is a CLEAN verdict — pixel
        # mismatches are suppressed entirely (no noise-driven repair work);
        # the region data remains visible in raster_stats for diagnostics.
        self.assertEqual(len(report.mismatches), 0)
        self.assertEqual(report.raster_stats["region_count"], 1)

    def test_region_attributed_to_overlapping_node(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 200, 100)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 200, "height": 100}}
        shot_bytes = _solid_png_bytes(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0))
        )
        base_bytes = _solid_png_bytes(800, 600, (255, 255, 255))
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", shot_bytes)
            base = tmp.write("base.png", base_bytes)
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        pixel_mismatches = [m for m in report.mismatches
                            if m["type"] == "pixel_mismatch"]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertEqual(pixel_mismatches[0]["node_id"], "n1")
        self.assertIn("region", pixel_mismatches[0]["expected"])
        self.assertIn("baseline_mae", pixel_mismatches[0]["expected"])
        self.assertIn("diff_percentage", pixel_mismatches[0]["actual"])
        self.assertEqual(report.raster_stats["region_count"], 1)

    def test_weighted_overall_score(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 200, 100)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 200, "height": 100}}
        shot_bytes = _solid_png_bytes(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0))
        )
        base_bytes = _solid_png_bytes(800, 600, (255, 255, 255))
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", shot_bytes)
            base = tmp.write("base.png", base_bytes)
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        # diffRatio = 20000/480000 = 1/24 → pixels = 23/24;
        # structural = 1.0 → overall = 0.85*1.0 + 0.15*(23/24)
        self.assertAlmostEqual(report.categories["pixels"], 1.0 - 1 / 24)
        self.assertAlmostEqual(
            report.similarity_score, 0.85 + 0.15 * (23 / 24)
        )

    def test_size_mismatch_emits_single_pixel_mismatch(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", _solid_png_bytes(8, 8, (0, 0, 0)))
            base = tmp.write("base.png", _solid_png_bytes(9, 8, (0, 0, 0)))
            report = DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
            )
        pixel_mismatches = [m for m in report.mismatches
                            if m["type"] == "pixel_mismatch"]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertEqual(pixel_mismatches[0]["reason"], "size_mismatch")
        self.assertEqual(pixel_mismatches[0]["node_id"], "n1")  # root fallback
        self.assertEqual(report.categories["pixels"], 0.0)
        self.assertEqual(report.raster_stats["diff_percentage"], 1.0)

    def test_missing_files_degrade_to_structural(self):
        plan = _MockPlan([_MockNode("n1", 0, 0, 100, 50)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 100, "height": 50}}
        report = DiffEngine().diff(
            plan, render_meta,
            render_screenshot="/nonexistent/shot.png",
            baseline_png="/nonexistent/base.png",
        )
        self.assertIsNone(report.raster_stats)
        self.assertEqual(report.similarity_score, 1.0)
        self.assertEqual(report.mismatches, [])


class TestRasterOptionsValidation(unittest.TestCase):
    def test_rejects_negative_color_threshold(self):
        with self.assertRaises(ValueError):
            RasterOptions(color_threshold=-1)

    def test_rejects_negative_min_region_area(self):
        with self.assertRaises(ValueError):
            RasterOptions(min_region_area=-1)

    def test_rejects_noise_floor_below_zero(self):
        with self.assertRaises(ValueError):
            RasterOptions(noise_floor=-0.01)

    def test_rejects_noise_floor_above_one(self):
        with self.assertRaises(ValueError):
            RasterOptions(noise_floor=1.5)

    def test_rejects_pixel_weight_below_zero(self):
        with self.assertRaises(ValueError):
            RasterOptions(pixel_weight=-0.1)

    def test_rejects_pixel_weight_above_one(self):
        with self.assertRaises(ValueError):
            RasterOptions(pixel_weight=1.5)

    def test_accepts_boundary_pixel_weights(self):
        self.assertEqual(RasterOptions(pixel_weight=0.0).pixel_weight, 0.0)
        self.assertEqual(RasterOptions(pixel_weight=1.0).pixel_weight, 1.0)


class TestDiffEnginePixelWeightBoundaries(unittest.TestCase):
    def _report(self, pixel_weight):
        # Geometry mismatch (render width off by 5) → structural = 0.0 for a
        # one-node plan; raster diff → diffRatio = 1/24 → pixels = 23/24.
        plan = _MockPlan([_MockNode("n1", 0, 0, 200, 100)])
        render_meta = {"n1": {"x": 0, "y": 0, "width": 205, "height": 100}}
        shot_bytes = _solid_png_bytes(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0))
        )
        base_bytes = _solid_png_bytes(800, 600, (255, 255, 255))
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", shot_bytes)
            base = tmp.write("base.png", base_bytes)
            return DiffEngine().diff(
                plan, render_meta,
                render_screenshot=str(shot), baseline_png=str(base),
                raster_options=RasterOptions(pixel_weight=pixel_weight),
            )

    def test_pixel_weight_zero_is_pure_structural(self):
        report = self._report(0.0)
        self.assertAlmostEqual(report.categories["pixels"], 1.0 - 1 / 24)
        self.assertAlmostEqual(report.similarity_score, 0.0)

    def test_pixel_weight_one_is_pure_pixel(self):
        report = self._report(1.0)
        self.assertAlmostEqual(report.categories["geometry"], 0.0)
        self.assertAlmostEqual(report.similarity_score, 1.0 - 1 / 24)


if __name__ == "__main__":
    unittest.main()
