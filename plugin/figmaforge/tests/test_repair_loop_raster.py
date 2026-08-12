"""
Repair-loop raster integration tests (Part 12).

Proves RepairLoop feeds render_fn screenshots + the configured baseline into
DiffEngine and that the capped pixel weight flows into the iteration score
and history — using a fake render_fn and encode_png-generated images (no
browser).

Run:  python3 -m unittest tests.test_repair_loop_raster -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.ir_types import IRDocument, IRNode, IRSource, KIND_FRAME, KIND_PAGE
from core.layout_types import Box, DISPLAY_FLEX, LayoutNodePlan, LayoutPlan
from core.png_codec import PngImage, encode_png
from core.repair_loop import RepairConfig, RepairLoop, STOP_THRESHOLD


def _make_plan():
    screen = LayoutNodePlan(
        node_id="frame-root", name="Root", kind="frame",
        display=DISPLAY_FLEX, box=Box(x=0, y=0, width=800, height=600),
    )
    screen.children.append(LayoutNodePlan(
        node_id="n1", name="Box", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=100),
    ))
    return LayoutPlan(file_key="fk", viewport=800.0, screens=[screen])


def _make_document():
    box = IRNode(
        id="n1", name="Box", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="n1"),
    )
    root = IRNode(
        id="frame-root", name="Root", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="fk", node_id="frame-root"),
        children=[box],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="fk", node_id="page-1"),
        children=[root],
    )
    return IRDocument(file_key="fk", name="Doc", pages=[page])


MATCHING_META = {
    "frame-root": {"x": 0, "y": 0, "width": 800, "height": 600},
    "n1": {"x": 0, "y": 0, "width": 200, "height": 100},
}


def _solid_png_bytes(width, height, rgb, rect=None):
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


class TestRepairLoopRasterIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        # Baseline: solid white 800x600
        self.baseline = self.dir / "baseline.png"
        self.baseline.write_bytes(_solid_png_bytes(800, 600, (255, 255, 255)))
        # Screenshot: white with a 200x100 red block over n1's bbox
        self.shot = self.dir / "shot.png"
        self.shot.write_bytes(_solid_png_bytes(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0))
        ))

    def _render_fn(self, plan, styles, document, iteration):
        return dict(MATCHING_META), str(self.shot)

    def test_pixel_weight_flows_into_score(self):
        config = RepairConfig(baseline_png=str(self.baseline))
        loop = RepairLoop(config=config, render_fn=self._render_fn)
        result = loop.run(_make_plan(), _make_document(), run_id="raster")
        # diffRatio = 20000/480000 = 1/24 → overall = 0.85 + 0.15*(23/24)
        self.assertAlmostEqual(result.final_score, 0.85 + 0.15 * (23 / 24))
        self.assertGreaterEqual(result.final_score, config.similarity_threshold)
        self.assertEqual(result.stop_reason, STOP_THRESHOLD)

    def test_raster_stats_flow_into_iteration_record(self):
        config = RepairConfig(baseline_png=str(self.baseline))
        loop = RepairLoop(config=config, render_fn=self._render_fn)
        result = loop.run(_make_plan(), _make_document(), run_id="raster-rec")
        record = result.history.iterations[0]
        diff_report = record.diff_report
        self.assertIsNotNone(diff_report["raster_stats"])
        self.assertEqual(diff_report["raster_stats"]["region_count"], 1)
        pixel_mismatches = [
            m for m in diff_report["mismatches"] if m["type"] == "pixel_mismatch"
        ]
        self.assertEqual(len(pixel_mismatches), 1)
        self.assertEqual(pixel_mismatches[0]["node_id"], "n1")
        self.assertEqual(record.screenshot_path, str(self.shot))

    def test_no_baseline_keeps_legacy_score(self):
        loop = RepairLoop(
            config=RepairConfig(),  # baseline_png stays None
            render_fn=self._render_fn,
        )
        result = loop.run(_make_plan(), _make_document(), run_id="legacy")
        self.assertEqual(result.final_score, 1.0)
        self.assertEqual(result.stop_reason, STOP_THRESHOLD)
        self.assertIsNone(result.history.iterations[0].diff_report["raster_stats"])

    def test_invalid_raster_knob_raises_at_config_time(self):
        with self.assertRaises(ValueError) as ctx:
            RepairConfig(pixel_weight=2.0)
        self.assertIn("invalid raster knob", str(ctx.exception))
        with self.assertRaises(ValueError):
            RepairConfig(color_threshold=-1)
        with self.assertRaises(ValueError):
            RepairConfig(noise_floor=1.5)
        with self.assertRaises(ValueError):
            RepairConfig(min_region_area=-8)

    def test_baseline_without_screenshot_degrades_to_structural(self):
        def _render_fn_no_shot(plan, styles, document, iteration):
            # Same combo as _default_render: meta matches, screenshot "".
            return dict(MATCHING_META), ""

        config = RepairConfig(baseline_png=str(self.baseline))
        loop = RepairLoop(config=config, render_fn=_render_fn_no_shot)
        result = loop.run(_make_plan(), _make_document(), run_id="no-shot")
        # No screenshot → raster diff skipped → legacy structural score.
        self.assertEqual(result.final_score, 1.0)
        self.assertEqual(result.stop_reason, STOP_THRESHOLD)
        self.assertIsNone(result.history.iterations[0].diff_report["raster_stats"])


if __name__ == "__main__":
    unittest.main()
