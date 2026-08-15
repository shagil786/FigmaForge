#!/usr/bin/env python3
"""
Regional SSIM gating tests (Part 13).

Verify DiffEngine._diff_raster's perceptual verdict: AA-style jitter that is
perceptually identical is suppressed (pixels stays 1.0, no pixel mismatches),
real localized changes are still reported, unmeasurable (sub-window) regions
are never suppressed, SSIM is always recorded as a diagnostic, the
ssim_enabled=False knob restores Part 12 pixel-count semantics, and the new
knobs are validated at config time.
"""
import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).parent.parent
sys.path.insert(0, str(plugin_root))

from core.diff_engine import DiffEngine, RasterOptions  # noqa: E402
from core.png_codec import PngImage, encode_png  # noqa: E402
from core.repair_loop import RepairConfig  # noqa: E402


def _png_bytes(width, height, pixel_fn, channels=3):
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            r, g, b = pixel_fn(x, y)
            if channels == 4:
                pixels.extend((r, g, b, 255))
            else:
                pixels.extend((r, g, b))
    return encode_png(PngImage(
        width=width, height=height, channels=channels, pixels=bytes(pixels),
    ))


def _solid(width, height, rgb, rect=None):
    return _png_bytes(
        width, height,
        lambda x, y: rect[4] if (rect is not None
                                 and rect[0] <= x < rect[0] + rect[2]
                                 and rect[1] <= y < rect[1] + rect[3])
        else rgb,
    )


class _MockBox:
    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.width = w
        self.height = h


class _MockNode:
    def __init__(self, nid, x, y, w, h):
        self.node_id = nid
        self.box = _MockBox(x, y, w, h)
        self.text = None

    def nodes(self):
        return [self]


class _MockPlan:
    def __init__(self, nodes):
        self._nodes = nodes

    def nodes(self):
        return self._nodes


def _page_meta(width, height):
    return {"n1": {"x": 0, "y": 0, "width": width, "height": height}}


def _page_plan(width, height):
    return _MockPlan([_MockNode("n1", 0, 0, width, height)])


# AA-noise fixture: identical hard content, but the 2-px antialiasing
# ramp of the stripe has +/-17 jitter on two columns (perceptually the
# same ramp; diffRatio 320/25600 = 1.25% above the 1% floor).
def _ramp_fn(jitter):
    def fn(x, y):
        if x < 49 or x >= 57:
            return (128, 128, 128)
        if 49 <= x < 53:
            if jitter and x == 50:
                return (67, 67, 67)
            if jitter and x == 51:
                return (101, 101, 101)
            return (84, 84, 84)
        return (40, 40, 40)
    return fn


class _RasterTmp:
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


class TestRegionalSsimGating(unittest.TestCase):
    def _diff(self, shot_bytes, base_bytes, options=None, size=(160, 160)):
        with _RasterTmp() as tmp:
            shot = tmp.write("shot.png", shot_bytes)
            base = tmp.write("base.png", base_bytes)
            return DiffEngine().diff(
                _page_plan(*size), _page_meta(*size),
                render_screenshot=str(shot), baseline_png=str(base),
                raster_options=options,
            )

    def test_aa_noise_suppressed_by_ssim(self):
        """Perceptually-identical AA jitter: clean verdict, no mismatches,
        pixels stays 1.0 despite diffRatio 1.25% > floor."""
        base = _png_bytes(160, 160, _ramp_fn(jitter=False))
        shot = _png_bytes(160, 160, _ramp_fn(jitter=True))
        report = self._diff(shot, base)
        self.assertEqual(report.categories["pixels"], 1.0)
        self.assertEqual(
            [m for m in report.mismatches if m["type"] == "pixel_mismatch"],
            [],
        )
        self.assertIn("ssim", report.raster_stats)
        self.assertGreaterEqual(report.raster_stats["min_region_ssim"], 0.95)
        self.assertIs(report.raster_stats["ssim_clean"], True)

    def test_real_localized_change_still_reported(self):
        """A real 30x30 color swap: mismatches emitted, pixels < 1.0."""
        base = _solid(200, 200, (128, 128, 128))
        shot = _solid(200, 200, (128, 128, 128),
                      rect=(65, 65, 30, 30, (0, 0, 0)))
        report = self._diff(shot, base, size=(200, 200))
        self.assertLess(report.categories["pixels"], 1.0)
        pixel_mismatches = [
            m for m in report.mismatches if m["type"] == "pixel_mismatch"
        ]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertEqual(pixel_mismatches[0]["node_id"], "n1")
        self.assertLess(report.raster_stats["min_region_ssim"], 0.5)
        self.assertIs(report.raster_stats["ssim_clean"], False)

    def test_global_ssim_high_but_regional_low(self):
        """The keystone: global SSIM stays high for a small strong change
        while the regional verdict (which is what gates) drops — the case a
        global-mean-only design would miss."""
        base = _solid(512, 384, (128, 128, 128))
        shot = _solid(512, 384, (128, 128, 128),
                      rect=(232, 168, 48, 48, (0, 0, 0)))
        report = self._diff(shot, base, size=(512, 384))
        self.assertGreaterEqual(report.raster_stats["ssim"], 0.9)
        self.assertLess(report.raster_stats["min_region_ssim"], 0.5)
        pixel_mismatches = [
            m for m in report.mismatches if m["type"] == "pixel_mismatch"
        ]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertIs(report.raster_stats["ssim_clean"], False)

    def test_tiny_region_never_suppressed(self):
        """A 2x4 region in a sub-window-width image cannot host the SSIM
        window → no verdict → treated as real (never suppressed)."""
        base = _solid(6, 40, (128, 128, 128))
        shot = _solid(6, 40, (128, 128, 128), rect=(2, 10, 2, 4, (0, 0, 0)))
        report = self._diff(shot, base, size=(6, 40))
        pixel_mismatches = [
            m for m in report.mismatches if m["type"] == "pixel_mismatch"
        ]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertIsNone(report.raster_stats["min_region_ssim"])
        self.assertIs(report.raster_stats["ssim_clean"], False)

    def test_edge_region_never_suppressed(self):
        """A region flush against a sub-window-height image axis → same
        conservative rule: mismatch emitted, no verdict."""
        base = _solid(30, 6, (128, 128, 128))
        shot = _solid(30, 6, (128, 128, 128), rect=(10, 0, 4, 4, (0, 0, 0)))
        report = self._diff(shot, base, size=(30, 6))
        pixel_mismatches = [
            m for m in report.mismatches if m["type"] == "pixel_mismatch"
        ]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertIsNone(report.raster_stats["min_region_ssim"])
        self.assertIs(report.raster_stats["ssim_clean"], False)

    def test_sub_floor_diff_still_records_ssim(self):
        """Below the noise floor the diff is clean, but SSIM is still
        computed and recorded (always-compute diagnostic, review fix F8)."""
        base = _solid(200, 200, (128, 128, 128))
        shot = _solid(200, 200, (128, 128, 128), rect=(10, 10, 8, 8, (0, 0, 0)))
        report = self._diff(shot, base, size=(200, 200))
        self.assertEqual(report.categories["pixels"], 1.0)
        self.assertIsInstance(report.raster_stats["ssim"], float)
        self.assertIsNone(report.raster_stats["min_region_ssim"])
        self.assertIs(report.raster_stats["ssim_clean"], True)

    def test_ssim_disabled_preserves_part12(self):
        """ssim_enabled=False restores pixel-count semantics: the AA jitter
        is no longer suppressed and no SSIM keys are emitted."""
        base = _png_bytes(160, 160, _ramp_fn(jitter=False))
        shot = _png_bytes(160, 160, _ramp_fn(jitter=True))
        report = self._diff(shot, base,
                            options=RasterOptions(ssim_enabled=False))
        pixel_mismatches = [
            m for m in report.mismatches if m["type"] == "pixel_mismatch"
        ]
        # The two jittered ramp columns are 4-connected → ONE 2x160 region.
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertLess(report.categories["pixels"], 1.0)
        self.assertNotIn("ssim", report.raster_stats)
        self.assertNotIn("ssim_clean", report.raster_stats)

    def test_size_mismatch_unchanged(self):
        """Different-sized inputs: single size_mismatch, clean False, no SSIM
        keys — SSIM is never computed on misaligned sizes."""
        base = _solid(200, 200, (128, 128, 128))
        shot = _solid(199, 200, (128, 128, 128))
        with _RasterTmp() as tmp:
            s = tmp.write("shot.png", shot)
            b = tmp.write("base.png", base)
            report = DiffEngine().diff(
                _page_plan(200, 200), _page_meta(200, 200),
                render_screenshot=str(s), baseline_png=str(b),
            )
        self.assertEqual(report.raster_stats["diff_percentage"], 1.0)
        self.assertEqual(len(report.mismatches), 1)
        self.assertEqual(report.mismatches[0]["reason"], "size_mismatch")
        self.assertEqual(report.categories["pixels"], 0.0)
        self.assertNotIn("ssim", report.raster_stats)
        self.assertNotIn("ssim_clean", report.raster_stats)

    def test_raster_stats_carries_ssim(self):
        """Any enabled raster run carries ssim, min_region_ssim and
        ssim_clean in raster_stats."""
        base = _solid(200, 200, (128, 128, 128))
        shot = _solid(200, 200, (128, 128, 128),
                      rect=(65, 65, 30, 30, (0, 0, 0)))
        report = self._diff(shot, base, size=(200, 200))
        self.assertIn("ssim", report.raster_stats)
        self.assertIn("min_region_ssim", report.raster_stats)
        self.assertIn("ssim_clean", report.raster_stats)

    def test_knob_validation(self):
        with self.assertRaises(ValueError):
            RasterOptions(ssim_threshold=1.5)
        with self.assertRaises(ValueError):
            RepairConfig(ssim_threshold=-1)
        with self.assertRaises(ValueError):
            RepairConfig(refresh_baseline=True, ssim_enabled=False)
        with self.assertRaises(ValueError):
            RepairConfig(max_baseline_refreshes_per_run=-1)


if __name__ == "__main__":
    unittest.main()
