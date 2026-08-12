"""
Render adapter tests (Part 11).

Proves the RepairLoop consumes the real harness render_meta shape through
the RenderCallable injection point — using a fake harness (no browser).

Run:  python3 -m unittest tests.test_render_adapter -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.generator_types import VStyle
from core.ir_types import IRDocument, IRNode, IRSource, KIND_FRAME, KIND_PAGE
from core.layout_types import Box, DISPLAY_FLEX, LayoutNodePlan, LayoutPlan
from core.render_adapter import make_render_callable
from core.render_harness import RenderHarnessError, RenderResult
from core.repair_loop import RepairConfig, RepairLoop, STOP_THRESHOLD


class FakeHarness:
    """Duck-typed RenderHarness that records calls and returns canned meta."""

    def __init__(self, meta):
        self.meta = meta
        self.calls = []

    def render(self, content_html, viewport_spec, build_id):
        self.calls.append({
            "html": content_html,
            "viewport": viewport_spec,
            "build_id": build_id,
        })
        return RenderResult(
            screenshot_path=Path(f"/tmp/figmaforge_fake/{build_id}.png"),
            layout_metadata=dict(self.meta),
        )


class FailingHarness:
    """Duck-typed RenderHarness whose render always raises."""

    def render(self, content_html, viewport_spec, build_id):
        raise RenderHarnessError("boom")


def _make_plan():
    """Screen 'frame-root' (viewport-sized) with child 'n1' at 0,0 200x100."""
    screen = LayoutNodePlan(
        node_id="frame-root", name="Root", kind="frame",
        display=DISPLAY_FLEX, box=Box(x=0, y=0, width=1440, height=900),
    )
    screen.children.append(LayoutNodePlan(
        node_id="n1", name="Box", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=100),
    ))
    return LayoutPlan(file_key="fk", viewport=1440.0, screens=[screen])


def _make_document():
    """Page containing frame 'frame-root' with child 'n1'."""
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


# render_meta matching the plan EXACTLY (similarity score 1.0). Both plan
# nodes — including the top-level screen node — must be present, otherwise
# DiffEngine reports missing_in_render.
MATCHING_META = {
    "frame-root": {"x": 0, "y": 0, "width": 1440, "height": 900},
    "n1": {"x": 0, "y": 0, "width": 200, "height": 100,
           "styles": {"fontSize": 16}},
}


class TestRenderAdapter(unittest.TestCase):
    def test_returns_diff_engine_shaped_meta(self):
        harness = FakeHarness(MATCHING_META)
        render_fn = make_render_callable(harness)
        meta, screenshot = render_fn(_make_plan(), {}, _make_document(), 0)
        self.assertEqual(screenshot, "/tmp/figmaforge_fake/repair-iter-0.png")
        self.assertIn("n1", meta)
        for key in ("x", "y", "width", "height"):
            self.assertIn(key, meta["n1"])
        self.assertEqual(len(harness.calls), 1)

    def test_generates_html_with_node_ids(self):
        harness = FakeHarness(MATCHING_META)
        render_fn = make_render_callable(harness)
        render_fn(
            _make_plan(),
            {"n1": VStyle(base={"width": "200px"})},
            _make_document(),
            0,
        )
        html = harness.calls[0]["html"]
        self.assertIn('data-node-id="frame-root"', html)
        self.assertIn('data-node-id="n1"', html)
        self.assertIn("width: 200px", html)

    def test_viewport_uses_plan_width(self):
        harness = FakeHarness(MATCHING_META)
        render_fn = make_render_callable(harness, default_height=800)
        plan = _make_plan()
        plan.viewport = 390.0
        render_fn(plan, {}, _make_document(), 1)
        self.assertEqual(
            harness.calls[0]["viewport"], {"width": 390, "height": 800}
        )
        self.assertEqual(harness.calls[0]["build_id"], "repair-iter-1")

    def test_repair_loop_consumes_adapter_meta(self):
        harness = FakeHarness(MATCHING_META)
        loop = RepairLoop(
            config=RepairConfig(similarity_threshold=0.95, max_iterations=3),
            render_fn=make_render_callable(harness),
        )
        result = loop.run(_make_plan(), _make_document(), run_id="adapter-test")
        self.assertEqual(result.stop_reason, STOP_THRESHOLD)
        self.assertEqual(result.final_score, 1.0)
        self.assertEqual(
            result.history.iterations[0].screenshot_path,
            "/tmp/figmaforge_fake/repair-iter-0.png",
        )

    def test_viewport_falls_back_on_zero_width(self):
        harness = FakeHarness(MATCHING_META)
        render_fn = make_render_callable(harness)
        plan = _make_plan()
        plan.viewport = 0.0
        render_fn(plan, {}, _make_document(), 0)
        self.assertEqual(
            harness.calls[0]["viewport"], {"width": 1440, "height": 900}
        )

    def test_harness_errors_propagate(self):
        render_fn = make_render_callable(FailingHarness())
        with self.assertRaises(RenderHarnessError):
            render_fn(_make_plan(), {}, _make_document(), 0)
        loop = RepairLoop(
            config=RepairConfig(similarity_threshold=0.95, max_iterations=3),
            render_fn=render_fn,
        )
        with self.assertRaises(RenderHarnessError):
            loop.run(_make_plan(), _make_document(), run_id="adapter-error")


if __name__ == "__main__":
    unittest.main()
