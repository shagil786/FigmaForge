#!/usr/bin/env python3
"""
Vue SFC backend tests (Part 14 Task 3).

Exercises the real .vue generator: file set + node coverage, no placeholder
markers, structural markers (<template>/<script setup>/<style scoped>), scoped
CSS lowering from the shared CssStyleGenerator (layout, fills, typography),
scoped @media breakpoint rules, fidelity-loss degradation, determinism, a
golden snapshot, and unchanged capabilities.

Run:  python3 -m unittest tests.test_vue_backend -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

plugin_root = Path(__file__).resolve().parent.parent
if str(plugin_root) not in sys.path:
    sys.path.insert(0, str(plugin_root))

from backends.protocol import GeneratedOutput
from backends.vue import VueBackend
from core.ir_types import (
    IRColor,
    IRDocument,
    IRFill,
    IRGradientStop,
    IRNode,
    IRPosition,
    IRSource,
    IRStyle,
    IRTextContent,
    IRTypography,
    KIND_FRAME,
    KIND_PAGE,
    KIND_TEXT,
)
from core.layout_types import (
    AlignmentSpec,
    AxisSizing,
    Box,
    BreakpointChange,
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_NONE,
    EdgeOffsets,
    LayoutNodePlan,
    LayoutPlan,
    SIZING_FIXED,
    SpacingSpec,
    SizingSpec,
    TextModel,
)

SNAPSHOT_DIR = plugin_root / "tests" / "snapshots" / "backends"
SNAPSHOT_NAME = "Landing.vue"
REWRITE_ENV = "REWRITE_SNAPSHOTS"


# ---------------------------------------------------------------------------
# Fixtures (same shapes as test_react_tailwind_backend.py — self-contained)
# ---------------------------------------------------------------------------

def _make_document() -> IRDocument:
    title = IRNode(
        id="t:1", name="Title", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="vue", node_id="t:1"),
        typography=IRTypography(
            font_family="Inter", font_size=32.0, font_weight=700.0,
            text_align="CENTER",
        ),
        text=IRTextContent(characters="Welcome"),
    )
    btn_label = IRNode(
        id="t:2", name="Label", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="vue", node_id="t:2"),
        typography=IRTypography(
            font_family="Inter", font_size=16.0, font_weight=600.0,
        ),
        text=IRTextContent(characters="Click me"),
    )
    button = IRNode(
        id="btn:1", name="Button", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="vue", node_id="btn:1"),
        style=IRStyle(
            fills=[IRFill(kind="solid", color=IRColor(r=0.2, g=0.4, b=0.8, a=1.0))],
            radius=8.0,
        ),
        children=[btn_label],
    )
    root = IRNode(
        id="0:1", name="Landing", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="vue", node_id="0:1"),
        children=[title, button],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="vue", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="vue", name="VueLanding", pages=[page])
    doc.root = root
    return doc


def _web_plan() -> LayoutPlan:
    screen = LayoutNodePlan(
        node_id="0:1", name="Landing", kind="frame",
        display=DISPLAY_FLEX, direction="column",
        box=Box(x=0, y=0, width=400, height=600),
        sizing=SizingSpec(
            horizontal=AxisSizing(mode=SIZING_FIXED),
            vertical=AxisSizing(mode=SIZING_FIXED),
        ),
        spacing=SpacingSpec(
            padding=EdgeOffsets(top=24, right=24, bottom=24, left=24),
            gap=24.0,
        ),
        alignment=AlignmentSpec(justify="CENTER", align="CENTER"),
    )
    title = LayoutNodePlan(
        node_id="t:1", name="Title", kind="text", display=DISPLAY_NONE,
        text=TextModel(characters="Welcome"),
    )
    button = LayoutNodePlan(
        node_id="btn:1", name="Button", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=120, height=48),
    )
    btn_label = LayoutNodePlan(
        node_id="t:2", name="Label", kind="text", display=DISPLAY_NONE,
        text=TextModel(characters="Click me"),
    )
    button.children.append(btn_label)
    screen.children.extend([title, button])
    return LayoutPlan(file_key="vue", viewport=1440.0, screens=[screen])


def _unsupported_fixture():
    """Gradient fill + absolute-positioned node (absolute is a vue loss)."""
    grad_node = IRNode(
        id="grad:1", name="Gradient", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="vue", node_id="grad:1"),
        style=IRStyle(fills=[IRFill(
            kind="gradient",
            gradient_stops=[
                IRGradientStop(position=0.0, color=IRColor(r=1.0, g=0.0, b=0.0, a=1.0)),
                IRGradientStop(position=1.0, color=IRColor(r=0.0, g=0.0, b=1.0, a=1.0)),
            ],
        )]),
    )
    abs_node = IRNode(
        id="abs:1", name="Badge", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="vue", node_id="abs:1"),
        position=IRPosition(mode="absolute", left=8.0, top=8.0),
    )
    root = IRNode(
        id="0:9", name="Overlay", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="vue", node_id="0:9"),
        children=[grad_node, abs_node],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="vue", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="vue", name="Overlay", pages=[page])
    doc.root = root

    screen = LayoutNodePlan(
        node_id="0:9", name="Overlay", kind="frame",
        display=DISPLAY_FLEX, direction="column",
        box=Box(x=0, y=0, width=400, height=300),
    )
    screen.children.append(LayoutNodePlan(
        node_id="grad:1", name="Gradient", kind="frame", display=DISPLAY_NONE,
        box=Box(x=0, y=0, width=200, height=100),
    ))
    screen.children.append(LayoutNodePlan(
        node_id="abs:1", name="Badge", kind="frame", display=DISPLAY_ABSOLUTE,
        box=Box(x=8, y=8, width=64, height=24),
    ))
    plan = LayoutPlan(file_key="vue", viewport=1440.0, screens=[screen])
    return doc, plan


class TestVueBackend(unittest.TestCase):
    def setUp(self):
        self.backend = VueBackend()
        self.doc = _make_document()
        self.plan = _web_plan()

    def _generate(self):
        return self.backend.generate(document=self.doc, layout_plan=self.plan)

    def _sfc(self):
        return [f for f in self._generate().files if f.path.endswith(".vue")][0]

    def test_generate_files_and_node_coverage(self):
        output = self._generate()
        self.assertIsInstance(output, GeneratedOutput)
        self.assertEqual(output.metadata["backend"], "vue")
        self.assertEqual(output.metadata["screen_count"], 1)

        vue_files = [f for f in output.files if f.path.endswith(".vue")]
        self.assertEqual(len(vue_files), 1)
        self.assertEqual(vue_files[0].path, "Landing.vue")
        self.assertEqual(vue_files[0].language, "vue")

        screen_ids = {n.node_id for n in self.plan.screens[0].walk() if n.node_id}
        self.assertTrue(screen_ids)
        self.assertEqual(set(vue_files[0].node_ids), screen_ids)

    def test_no_placeholder_markers(self):
        self.assertNotIn("TODO: Generate from LayoutPlan", self._sfc().content)

    def test_structural_markers(self):
        content = self._sfc().content
        self.assertIn("<template>", content)
        self.assertIn("</template>", content)
        self.assertIn("<script setup", content)
        self.assertIn("defineProps", content)
        self.assertIn("<style scoped>", content)
        self.assertIn("</style>", content)

    def test_style_lowering(self):
        content = self._sfc().content
        # Root layout from the shared CssStyleGenerator.
        self.assertIn("display: flex", content)
        self.assertIn("flex-direction: column", content)
        self.assertIn("justify-content: center", content)
        self.assertIn("align-items: center", content)
        self.assertIn("gap: 24px", content)
        self.assertIn("padding-top: 24px", content)
        self.assertIn("width: 400px", content)
        # Button fill + radius from the IR.
        self.assertIn("background: #3366cc", content)
        self.assertIn("border-radius: 8px", content)

    def test_typography_lowered(self):
        content = self._sfc().content
        self.assertIn("font-size: 32px", content)
        self.assertIn("font-weight: 700", content)
        self.assertIn("font-family: Inter", content)
        self.assertIn("text-align: center", content)

    def test_breakpoints_mapped(self):
        self.plan.screens[0].breakpoints.append(BreakpointChange(
            breakpoint="md", width=768.0, node_id="0:1",
            property="direction", before="column", after="row",
            evidence="measured",
        ))
        content = self._sfc().content
        self.assertIn("@media (max-width: 768px)", content)
        self.assertIn("flex-direction: row", content)

    def test_unsupported_features_losses(self):
        doc, plan = _unsupported_fixture()
        output = self.backend.generate(document=doc, layout_plan=plan)
        losses = [l for l in output.fidelity_losses if l.node_id == "abs:1"]
        self.assertTrue(losses, "absolute positioning should be a preflight loss")
        self.assertEqual(losses[0].feature, "absolute_positioning")

        sfc = [f for f in output.files if f.path.endswith(".vue")][0]
        # Template degrades with an inline marker, without crashing.
        self.assertIn("<!-- fidelity: absolute_positioning", sfc.content)
        # Gradient fills are genuinely representable in scoped CSS.
        self.assertIn("linear-gradient", sfc.content)

    def test_deterministic(self):
        a = self._sfc().content
        b = self._sfc().content
        self.assertEqual(a, b)

    def test_golden_snapshot(self):
        snapshot_path = SNAPSHOT_DIR / SNAPSHOT_NAME

        if os.environ.get(REWRITE_ENV) == "1":
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(self._sfc().content, encoding="utf-8")
            self.skipTest(f"Snapshot regenerated at {snapshot_path}")

        if not snapshot_path.exists():
            self.fail(
                f"Snapshot {snapshot_path} is missing. "
                f"Run with {REWRITE_ENV}=1 to generate it."
            )
        self.assertEqual(
            self._sfc().content,
            snapshot_path.read_text(encoding="utf-8"),
            "Generated SFC no longer matches the snapshot. If the change is "
            f"intended, regenerate with {REWRITE_ENV}=1 and review the diff.",
        )

    def test_capabilities_unchanged(self):
        caps = self.backend.capabilities
        self.assertEqual(caps.styling_system, "css_scoped")
        self.assertEqual(caps.framework, "vue")
        self.assertEqual(caps.renderer, "browser")
        self.assertEqual(caps.file_extensions, (".vue",))
        self.assertIn("flex", caps.supported_features)
        self.assertIn("absolute_positioning", caps.unsupported_features)


if __name__ == "__main__":
    unittest.main(verbosity=2)
