#!/usr/bin/env python3
"""
Baseline auto-refresh tests (Part 13).

Verify RepairLoop adopts clean renders as the new baseline ONLY when opt-in,
only on a clean SSIM verdict, never on real regressions or size mismatches,
never when byte-identical (no churn), and at most
``max_baseline_refreshes_per_run`` times per run — always via versioned
sibling files that leave the original Figma baseline untouched.
"""
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
from core.repair_loop import RepairConfig, RepairLoop, STOP_MAX_ITERATIONS


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


class TestBaselineRefresh(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.baseline = self.dir / "baseline.png"
        self.baseline_bytes = _solid_png_bytes(800, 600, (255, 255, 255))
        self.baseline.write_bytes(self.baseline_bytes)
        self.original_baseline_bytes = self.baseline_bytes

    def refreshed_files(self):
        return sorted(self.dir.glob("baseline.refreshed.*.png"))

    def refresh_event_count(self, result):
        count = 0
        for record in result.history.iterations:
            stats = record.diff_report.get("raster_stats") or {}
            if stats.get("baseline_refreshed"):
                count += 1
        return count

    def _drift_render(self, mismatch=False):
        """Clean renders (byte-different each call, always sub-threshold vs
        any baseline) — a 2x2 light-gray block drifts; delta 15 < the 16
        color threshold, so no diff pixels ever appear."""
        state = {"calls": 0}

        def _render_fn(plan, styles, document, iteration):
            state["calls"] += 1
            meta = dict(MATCHING_META)
            if mismatch:
                # Persistent geometry deviation keeps the overall score below
                # the threshold so the loop runs multiple iterations.
                meta["n1"] = {"x": 0, "y": 0, "width": 205, "height": 100}
            bx = (state["calls"] * 7) % 790
            by = (state["calls"] * 11) % 590
            png = _solid_png_bytes(
                800, 600, (255, 255, 255),
                rect=(bx, by, 2, 2, (240, 240, 240)),
            )
            shot = self.dir / f"shot-{state['calls']}.png"
            shot.write_bytes(png)
            return meta, str(shot)

        return _render_fn

    def test_refresh_disabled_by_default(self):
        config = RepairConfig(baseline_png=str(self.baseline))
        loop = RepairLoop(config=config, render_fn=self._drift_render())
        result = loop.run(_make_plan(), _make_document(), run_id="off")
        self.assertEqual(self.refreshed_files(), [])
        self.assertEqual(
            self.baseline.read_bytes(), self.original_baseline_bytes,
        )
        self.assertEqual(self.refresh_event_count(result), 0)

    def test_clean_render_adopts_new_baseline(self):
        config = RepairConfig(
            baseline_png=str(self.baseline), refresh_baseline=True,
        )
        render = self._drift_render()
        loop = RepairLoop(config=config, render_fn=render)
        result = loop.run(_make_plan(), _make_document(), run_id="adopt")

        refreshed = self.refreshed_files()
        self.assertEqual(len(refreshed), 1)
        self.assertEqual(refreshed[0].name, "baseline.refreshed.0.png")
        # Adopted content == the first render's screenshot.
        self.assertEqual(
            refreshed[0].read_bytes(),
            (self.dir / "shot-1.png").read_bytes(),
        )
        # Config repointed at the adopted baseline.
        self.assertEqual(config.baseline_png, str(refreshed[0]))
        # The ORIGINAL Figma baseline is byte-identical — provenance kept.
        self.assertEqual(
            self.baseline.read_bytes(), self.original_baseline_bytes,
        )
        # The adoption is observable in the iteration record.
        self.assertEqual(self.refresh_event_count(result), 1)
        stats = result.history.iterations[0].diff_report["raster_stats"]
        self.assertIs(stats["baseline_refreshed"], True)
        self.assertEqual(stats["baseline_new_path"], str(refreshed[0]))

    def test_regression_never_refreshed(self):
        shot = self.dir / "shot-red.png"
        shot.write_bytes(_solid_png_bytes(
            800, 600, (255, 255, 255), rect=(0, 0, 200, 100, (255, 0, 0)),
        ))

        def _render_fn(plan, styles, document, iteration):
            return dict(MATCHING_META), str(shot)

        config = RepairConfig(
            baseline_png=str(self.baseline), refresh_baseline=True,
        )
        loop = RepairLoop(config=config, render_fn=_render_fn)
        result = loop.run(_make_plan(), _make_document(), run_id="regress")
        self.assertEqual(self.refreshed_files(), [])
        self.assertEqual(
            self.baseline.read_bytes(), self.original_baseline_bytes,
        )
        self.assertEqual(self.refresh_event_count(result), 0)

    def test_refresh_bounded_and_versioned(self):
        config = RepairConfig(
            baseline_png=str(self.baseline),
            refresh_baseline=True,
            max_baseline_refreshes_per_run=3,
            max_iterations=6,
            min_progress=0.0,
        )
        loop = RepairLoop(
            config=config, render_fn=self._drift_render(mismatch=True),
        )
        result = loop.run(_make_plan(), _make_document(), run_id="bounded")

        refreshed = self.refreshed_files()
        self.assertEqual(len(refreshed), 3)
        self.assertEqual(
            [p.name for p in refreshed],
            ["baseline.refreshed.0.png", "baseline.refreshed.1.png",
             "baseline.refreshed.2.png"],
        )
        # Versioned files hold distinct content (each render drifted).
        contents = {p.read_bytes() for p in refreshed}
        self.assertEqual(len(contents), 3)
        # Config points at the newest adoption.
        self.assertEqual(config.baseline_png, str(refreshed[-1]))
        # Budget respected: exactly 3 refresh events across the run.
        self.assertEqual(self.refresh_event_count(result), 3)
        # The original baseline is never touched.
        self.assertEqual(
            self.baseline.read_bytes(), self.original_baseline_bytes,
        )
        self.assertEqual(result.stop_reason, STOP_MAX_ITERATIONS)

    def test_size_mismatch_never_refreshes(self):
        shot = self.dir / "shot-small.png"
        shot.write_bytes(_solid_png_bytes(799, 600, (255, 255, 255)))

        def _render_fn(plan, styles, document, iteration):
            return dict(MATCHING_META), str(shot)

        config = RepairConfig(
            baseline_png=str(self.baseline), refresh_baseline=True,
        )
        loop = RepairLoop(config=config, render_fn=_render_fn)
        result = loop.run(_make_plan(), _make_document(), run_id="sizemismatch")
        self.assertEqual(self.refreshed_files(), [])
        self.assertEqual(
            self.baseline.read_bytes(), self.original_baseline_bytes,
        )
        self.assertEqual(self.refresh_event_count(result), 0)
        stats = result.history.iterations[0].diff_report["raster_stats"]
        self.assertEqual(stats["diff_percentage"], 1.0)

    def test_no_churn_on_identical_renders(self):
        shot = self.dir / "shot-same.png"
        shot.write_bytes(self.original_baseline_bytes)

        def _render_fn(plan, styles, document, iteration):
            return dict(MATCHING_META), str(shot)

        config = RepairConfig(
            baseline_png=str(self.baseline), refresh_baseline=True,
        )
        loop = RepairLoop(config=config, render_fn=_render_fn)
        result = loop.run(_make_plan(), _make_document(), run_id="nochurn")
        # Byte-identical render == baseline → nothing to adopt, no churn.
        self.assertEqual(self.refreshed_files(), [])
        self.assertEqual(self.refresh_event_count(result), 0)


if __name__ == "__main__":
    unittest.main()
