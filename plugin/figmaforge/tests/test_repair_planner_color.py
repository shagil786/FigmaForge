"""
Repair-planner color extraction tests (Part 20, Task 1).

A pixel_mismatch candidate must be repaired with the *actual* baseline
color in the attributed region — the old behavior produced a non-color
patch value (the region dict + baseline_mae) that could never improve the
render.  The planner now extracts the baseline's mean RGB over the region
and patches ``background``.  Degrade paths (no baseline / corrupt
baseline / no region) keep the legacy behavior — never a crash.

Run:  python3 -m unittest tests.test_repair_planner_color -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.diff_engine import DiffReport
from core.ir_types import IRDocument, IRNode, IRSource, KIND_FRAME, KIND_PAGE
from core.layout_types import Box, DISPLAY_FLEX, LayoutNodePlan, LayoutPlan
from core.png_codec import PngError, PngImage, decode_png, encode_png
from core.patch_planner import (
    TARGET_STYLE,
    PatchPlanner,
)
from core.repair_classifier import CATEGORY_COLOR, RepairClassifier
from core.repair_loop import RepairConfig, RepairLoop, STOP_THRESHOLD


# ---------------------------------------------------------------------------
# Shared scaffolding (mirrors test_repair_loop_raster)
# ---------------------------------------------------------------------------


def _make_plan() -> LayoutPlan:
    screen = LayoutNodePlan(
        node_id="frame-root", name="Root", kind="frame",
        display=DISPLAY_FLEX, box=Box(x=0, y=0, width=800, height=600),
    )
    screen.children.append(LayoutNodePlan(
        node_id="n1", name="Box", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=100),
    ))
    return LayoutPlan(file_key="fk", viewport=800.0, screens=[screen])


def _make_document() -> IRDocument:
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


def _solid_png(width: int, height: int, rgb, rect=None) -> bytes:
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


def _pixel_mismatch_report(region: dict) -> DiffReport:
    """DiffReport with a single pixel_mismatch attributed to n1."""
    return DiffReport(
        similarity_score=0.5,
        categories={"geometry": 1.0, "style": 1.0, "pixels": 0.5},
        mismatches=[{
            "node_id": "n1",
            "type": "pixel_mismatch",
            "expected": {
                "region": region,
                "baseline_mae": {"r": 12.5, "g": 3.0, "b": 2.0},
            },
            "actual": {"diff_percentage": 0.041},
        }],
    )


def _classify_and_plan(report: DiffReport, baseline_png=None):
    """Run a report through the classifier + planner the loop would use."""
    plan = _make_plan()
    document = _make_document()
    classification = RepairClassifier(
        plan=plan, document=document,
    ).classify(report)
    candidate = classification.candidates[0]
    self_check = CATEGORY_COLOR
    assert candidate.category == self_check  # test scaffolding invariant
    plan_out = PatchPlanner(
        plan=plan, document=document, baseline_png=baseline_png,
    ).plan(classification)
    return plan_out


class TestPlannerColorExtraction(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_pixel_mismatch_patches_background_with_baseline_color(self):
        baseline = self.dir / "base.png"
        baseline.write_bytes(_solid_png(800, 600, (255, 255, 255)))
        plan_out = _classify_and_plan(
            _pixel_mismatch_report(
                {"x": 0, "y": 0, "width": 200, "height": 100, "area": 20000},
            ),
            baseline_png=str(baseline),
        )
        self.assertEqual(len(plan_out.patches), 1)
        patch = plan_out.patches[0]
        self.assertEqual(patch.target_type, TARGET_STYLE)
        self.assertEqual(patch.target_key, "n1")
        self.assertEqual(patch.property_name, "background")
        self.assertEqual(patch.new_value, "#ffffff")

    def test_region_color_is_the_mean_across_the_region(self):
        # 2x2 baseline: top row red, bottom row white → mean #ff8080.
        baseline = self.dir / "mixed.png"
        baseline.write_bytes(_solid_png(
            2, 2, (255, 255, 255),
            rect=(0, 0, 2, 1, (255, 0, 0)),
        ))
        plan_out = _classify_and_plan(
            _pixel_mismatch_report(
                {"x": 0, "y": 0, "width": 2, "height": 2, "area": 4},
            ),
            baseline_png=str(baseline),
        )
        patch = plan_out.patches[0]
        self.assertEqual(patch.property_name, "background")
        self.assertEqual(patch.new_value, "#ff8080")

    def test_region_clamped_to_image_bounds(self):
        # Region starts off-canvas; the whole 8x8 teal image is averaged.
        baseline = self.dir / "teal.png"
        baseline.write_bytes(_solid_png(8, 8, (0, 255, 128)))
        plan_out = _classify_and_plan(
            _pixel_mismatch_report(
                {"x": -5, "y": -5, "width": 10, "height": 10, "area": 100},
            ),
            baseline_png=str(baseline),
        )
        patch = plan_out.patches[0]
        self.assertEqual(patch.new_value, "#00ff80")

    def test_no_baseline_keeps_legacy_region_dict_patch(self):
        # baseline_png=None → legacy behavior: value stays the expected
        # dict and the property stays "color" (honest degrade, no crash).
        plan_out = _classify_and_plan(_pixel_mismatch_report(
            {"x": 0, "y": 0, "width": 200, "height": 100, "area": 20000},
        ))
        patch = plan_out.patches[0]
        self.assertEqual(patch.property_name, "color")
        self.assertIsInstance(patch.new_value, dict)
        self.assertIn("region", patch.new_value)
        self.assertIn("baseline_mae", patch.new_value)

    def test_corrupt_baseline_falls_back_to_legacy(self):
        baseline = self.dir / "corrupt.png"
        baseline.write_bytes(b"not a png at all")
        with self.assertRaises(PngError):
            decode_png(baseline.read_bytes())  # scaffold: input is corrupt
        plan_out = _classify_and_plan(_pixel_mismatch_report(
            {"x": 0, "y": 0, "width": 200, "height": 100, "area": 20000},
        ), baseline_png=str(baseline))
        patch = plan_out.patches[0]
        self.assertEqual(patch.property_name, "color")
        self.assertIsInstance(patch.new_value, dict)


class TestRepairLoopAppliesRealColorPatch(unittest.TestCase):
    """End-to-end: the loop's pixel mismatch → real #rrggbb style patch
    → the render converges (threshold satisfied). Uses a fake render_fn
    (no browser), threshold 1.0 so the sub-threshold first score is
    actually below the gate and the patch must apply."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.baseline = self.dir / "baseline.png"
        self.baseline.write_bytes(_solid_png(800, 600, (255, 255, 255)))
        self.shot = self.dir / "shot.png"
        self.shot.write_bytes(_solid_png(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0)),
        ))

    def _render_fn(self, plan, styles, document, iteration):
        # Once n1's background is patched to the baseline's white, the
        # render matches the baseline.
        n1_style = styles.get("n1")
        bg = n1_style.base.get("background") if n1_style is not None else None
        if bg == "#ffffff":
            return dict(MATCHING_META), str(self.baseline)
        return dict(MATCHING_META), str(self.shot)

    def test_loop_repairs_background_and_converges(self):
        config = RepairConfig(
            similarity_threshold=1.0,
            max_iterations=5,
            baseline_png=str(self.baseline),
        )
        from core.generator_types import VStyle
        styles = {"n1": VStyle(base={})}
        loop = RepairLoop(config=config, render_fn=self._render_fn)
        result = loop.run(
            _make_plan(), _make_document(), styles=styles, run_id="color-fix",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.stop_reason, STOP_THRESHOLD)
        self.assertEqual(result.final_score, 1.0)
        self.assertGreaterEqual(result.iterations_run, 1)

        # The patch that got applied is a real background color.
        record = result.history.iterations[0]
        self.assertIsNotNone(record.execution_result)
        applied = record.execution_result["applied"]
        self.assertEqual(len(applied), 1)
        mutation = applied[0]
        self.assertEqual(mutation["target_type"], TARGET_STYLE)
        self.assertEqual(mutation["property_name"], "background")
        self.assertEqual(mutation["new_value"], "#ffffff")

        # The in-memory style was genuinely repaired.
        self.assertEqual(styles["n1"].base.get("background"), "#ffffff")


if __name__ == "__main__":
    unittest.main()
