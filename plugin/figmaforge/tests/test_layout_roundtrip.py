"""
LayoutPlan JSON round-trip tests (Part 16, Task 1).

Same contract as the IR loader: ``LayoutPlan.from_dict(x.to_dict()).to_dict()``
must equal ``x.to_dict()`` exactly (floats are already rounded by ``_round``
in ``to_dict``, so the artifact-stability guarantee is the JSON form).

Run:  python3 -m unittest tests.test_layout_roundtrip -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from core.figma_fixtures import FixtureLoader  # noqa: E402
from core.figma_types import FigmaFile  # noqa: E402
from core.ir_builder import IRBuilder  # noqa: E402
from core.layout_analyzer import LayoutAnalyzer  # noqa: E402
from core.library_types import LibraryLoader  # noqa: E402
from core.layout_types import (  # noqa: E402
    AlignmentSpec,
    Anchoring,
    Box,
    BreakpointChange,
    BreakpointPlan,
    ConstraintReport,
    Diagnostic,
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    LayoutNodePlan,
    LayoutPlan,
    OverflowSpec,
    SIZING_FIXED,
    SIZING_HUG,
    SpacingSpec,
    TextModel,
)

FIXTURES = {
    "layout_desktop": "lay1440",
    "layout_mobile": "mobile",
    "layout_nested": "nested",
}


def build_plan(fixture: str, file_key: str) -> LayoutPlan:
    loader = FixtureLoader(plugin_root / "fixtures" / "figma")
    raw = loader.load(fixture)
    doc = IRBuilder().build(FigmaFile.from_dict(file_key, raw))
    return LayoutAnalyzer().analyze(doc, library=LibraryLoader().load())


def _single_node_plan() -> LayoutPlan:
    """A minimal plan exercising the leaf value objects a fixture may not:
    absolute display, anchors, spacing with margins, overflow, text model,
    constraints report, diagnostics, and a breakpoint change."""
    child = LayoutNodePlan(
        node_id="2:1",
        name="Abs",
        kind="frame",
        display=DISPLAY_ABSOLUTE,
        order=0,
        box=Box(x=12.0, y=24.0, width=100.0, height=50.0),
        figma_box=Box(x=12.0, y=24.0, width=100.0, height=50.0),
        bounds_delta=0.0,
        sizing=None,
        spacing=SpacingSpec(margin_source="absolute offsets"),
        alignment=AlignmentSpec(align_self="MIN"),
        anchors=Anchoring(horizontal="min", vertical="min", left=12.0, top=24.0),
        text=None,
        overflow=OverflowSpec(x="visible", y="clip", wrap="wrap"),
        breakpoints=[BreakpointChange(breakpoint="md", width=768.0, node_id="2:1", property="width", before="100", after="120", evidence="fixture")],
        confidence=0.5,
        assumptions=["absolute offsets"],
        constraints=ConstraintReport(total=2, grounding=2),
        diagnostics=[Diagnostic(severity="info", code="TEST", message="single-node", node_id="2:1")],
        children=[],
    )
    root = LayoutNodePlan(
        node_id="1:1",
        name="Screen",
        kind="frame",
        display=DISPLAY_FLEX,
        direction="column",
        order=0,
        box=Box(x=0.0, y=0.0, width=1440.0, height=900.0),
        sizing=None,
        text=TextModel(
            characters="Hero",
            font_size=32.0,
            measured_width=120.0,
            measured_height=40.0,
            wrapped=True,
            lines=["Hero"],
            approximate=True,
        ),
        overflow=OverflowSpec(x="visible", y="visible"),
        children=[child],
    )
    return LayoutPlan(
        schema_version=1,
        file_key="single",
        viewport=1440.0,
        base_width=1440.0,
        source={"file_key": "single"},
        screens=[root],
        breakpoints=BreakpointPlan(
            breakpoints=[{"name": "base", "width": 1440.0}, {"name": "md", "width": 768.0}],
            changes=[BreakpointChange(breakpoint="md", width=768.0, node_id="2:1", property="width")],
            no_change=["1:1"],
        ),
        counts={"screens": 1, "nodes": 2},
        confidence={"overall": 0.9},
        diagnostics=[Diagnostic(severity="warning", code="ABS_OFFSET", message="x")],
    )


class TestLayoutRoundTrip(unittest.TestCase):
    def _assert_roundtrips(self, plan: LayoutPlan) -> None:
        original = plan.to_dict()
        reloaded = LayoutPlan.from_dict(original)
        self.assertEqual(reloaded.to_dict(), original)

    def test_layout_roundtrip_desktop(self):
        self._assert_roundtrips(build_plan("layout_desktop", "lay1440"))

    def test_layout_roundtrip_mobile(self):
        self._assert_roundtrips(build_plan("layout_mobile", "mobile"))

    def test_layout_roundtrip_nested(self):
        self._assert_roundtrips(build_plan("layout_nested", "nested"))

    def test_layout_roundtrip_single_node(self):
        self._assert_roundtrips(_single_node_plan())

    def test_layout_roundtrip_preserves_tree(self):
        """The reloaded plan has the same node tree shape as the original."""
        plan = build_plan("layout_desktop", "lay1440")
        reloaded = LayoutPlan.from_dict(plan.to_dict())

        def count_nodes(plan_node: LayoutNodePlan) -> int:
            return 1 + sum(count_nodes(c) for c in plan_node.children)

        for original, reloaded_screen in zip(plan.screens, reloaded.screens):
            self.assertEqual(count_nodes(original), count_nodes(reloaded_screen))
            self.assertEqual(original.node_id, reloaded_screen.node_id)


if __name__ == "__main__":
    unittest.main()
