#!/usr/bin/env python3
"""
SwiftUI backend tests (Part 14 Task 5).

Exercises the real .swift view generator: file set + node coverage, no
placeholder markers, structural markers (struct …View: View / #Preview /
var body), VStack/HStack container lowering, the SwiftUI modifier chain
(frame/padding/background/cornerRadius/opacity/foregroundColor), typography
modifiers (font/multilineTextAlignment/kerning/lineSpacing), fidelity-loss
degradation with // fidelity: markers, determinism, a golden snapshot, and
unchanged capabilities.

Run:  python3 -m unittest tests.test_swiftui_backend -v
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
from backends.swiftui import SwiftUIBackend
from core.ir_types import (
    IRColor,
    IRDocument,
    IRFill,
    IRGradientStop,
    IRNode,
    IRPosition,
    IRShadow,
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
    DISPLAY_ABSOLUTE,
    DISPLAY_FLEX,
    DISPLAY_NONE,
    EdgeOffsets,
    LayoutNodePlan,
    LayoutPlan,
    OVERFLOW_CLIP,
    OverflowSpec,
    SIZING_FILL,
    SIZING_FIXED,
    SpacingSpec,
    SizingSpec,
    TextModel,
)

SNAPSHOT_DIR = plugin_root / "tests" / "snapshots" / "backends"
SNAPSHOT_NAME = "LandingView.swift"
REWRITE_ENV = "REWRITE_SNAPSHOTS"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_document() -> IRDocument:
    title = IRNode(
        id="t:1", name="Title", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="swift", node_id="t:1"),
        style=IRStyle(fills=[IRFill(
            kind="solid", color=IRColor(r=0.08, g=0.12, b=0.24, a=1.0),
        )]),
        typography=IRTypography(
            font_family="Inter", font_size=32.0, font_weight=700.0,
            text_align="CENTER",
        ),
        text=IRTextContent(characters="Welcome"),
    )
    btn_label = IRNode(
        id="t:2", name="Label", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="swift", node_id="t:2"),
        typography=IRTypography(
            font_family="Inter", font_size=16.0, font_weight=600.0,
            line_height=24.0, letter_spacing=0.5,
        ),
        text=IRTextContent(characters="Click me"),
    )
    button = IRNode(
        id="btn:1", name="Button", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="swift", node_id="btn:1"),
        style=IRStyle(
            fills=[IRFill(kind="solid", color=IRColor(r=0.2, g=0.4, b=0.8, a=1.0))],
            radius=8.0,
            opacity=0.9,
        ),
        children=[btn_label],
    )
    root = IRNode(
        id="0:1", name="Landing", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="swift", node_id="0:1"),
        children=[title, button],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="swift", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="swift", name="SwiftLanding", pages=[page])
    doc.root = root
    return doc


def _swift_plan() -> LayoutPlan:
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
        alignment=AlignmentSpec(justify="CENTER", align="MAX"),
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
    return LayoutPlan(file_key="swift", viewport=390.0, screens=[screen])


def _justify_plan(justify: str) -> LayoutPlan:
    """A column screen with three leaf children and a main-axis justification."""
    screen = LayoutNodePlan(
        node_id="0:1", name="Justify", kind="frame",
        display=DISPLAY_FLEX, direction="column",
        box=Box(x=0, y=0, width=400, height=600),
        sizing=SizingSpec(
            horizontal=AxisSizing(mode=SIZING_FIXED),
            vertical=AxisSizing(mode=SIZING_FIXED),
        ),
        alignment=AlignmentSpec(justify=justify, align="CENTER"),
    )
    for i in range(3):
        screen.children.append(LayoutNodePlan(
            node_id=f"c:{i}", name=f"Child{i}", kind="frame",
            display=DISPLAY_NONE, box=Box(x=0, y=0, width=100, height=30),
        ))
    return LayoutPlan(file_key="swift", viewport=390.0, screens=[screen])


def _justify_document() -> IRDocument:
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="swift", node_id="page-1"),
    )
    return IRDocument(file_key="swift", name="Justify", pages=[page])


def _rich_fixture():
    """Shadows, text decoration, overflow clip, and fill sizing."""
    label = IRNode(
        id="t:3", name="Label", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="swift", node_id="t:3"),
        typography=IRTypography(
            font_family="Inter", font_size=14.0, font_weight=600.0,
            text_decoration="UNDERLINE",
        ),
        text=IRTextContent(characters="Save changes"),
    )
    card = IRNode(
        id="card:1", name="Card", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="swift", node_id="card:1"),
        style=IRStyle(
            fills=[IRFill(kind="solid", color=IRColor(r=0.1, g=0.1, b=0.1, a=1.0))],
            shadows=[IRShadow(
                color=IRColor(r=0.0, g=0.0, b=0.0, a=0.25), x=0.0, y=4.0, blur=8.0,
            )],
        ),
        children=[label],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="swift", node_id="page-1"),
        children=[card],
    )
    doc = IRDocument(file_key="swift", name="Rich", pages=[page])
    doc.root = card

    card_plan = LayoutNodePlan(
        node_id="card:1", name="Card", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=80),
        sizing=SizingSpec(
            horizontal=AxisSizing(mode=SIZING_FILL),
            vertical=AxisSizing(mode=SIZING_FIXED),
        ),
        overflow=OverflowSpec(x=OVERFLOW_CLIP, y=OVERFLOW_CLIP),
    )
    label_plan = LayoutNodePlan(
        node_id="t:3", name="Label", kind="text", display=DISPLAY_NONE,
        text=TextModel(characters="Save changes"),
    )
    card_plan.children.append(label_plan)
    plan = LayoutPlan(file_key="swift", viewport=390.0, screens=[card_plan])
    return doc, plan


def _unsupported_fixture():
    """Gradient + absolute + image-fill nodes (image fill is the swiftui loss)."""
    grad_node = IRNode(
        id="grad:1", name="Gradient", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="swift", node_id="grad:1"),
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
        source=IRSource(file_key="swift", node_id="abs:1"),
        position=IRPosition(mode="absolute", left=8.0, top=8.0),
        style=IRStyle(fills=[IRFill(
            kind="solid", color=IRColor(r=1.0, g=0.0, b=0.0, a=1.0),
        )]),
    )
    img_node = IRNode(
        id="img:1", name="Photo", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="swift", node_id="img:1"),
        style=IRStyle(fills=[IRFill(kind="image", image_ref="asset://photo")]),
    )
    root = IRNode(
        id="0:9", name="Overlay", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="swift", node_id="0:9"),
        children=[grad_node, abs_node, img_node],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="swift", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="swift", name="Overlay", pages=[page])
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
    screen.children.append(LayoutNodePlan(
        node_id="img:1", name="Photo", kind="frame", display=DISPLAY_NONE,
        box=Box(x=0, y=0, width=100, height=50),
    ))
    plan = LayoutPlan(file_key="swift", viewport=390.0, screens=[screen])
    return doc, plan


class TestSwiftUIBackend(unittest.TestCase):
    def setUp(self):
        self.backend = SwiftUIBackend()
        self.doc = _make_document()
        self.plan = _swift_plan()

    def _generate(self):
        return self.backend.generate(document=self.doc, layout_plan=self.plan)

    def _view(self):
        return [f for f in self._generate().files if f.path.endswith(".swift")][0]

    def test_generate_files_and_node_coverage(self):
        output = self._generate()
        self.assertIsInstance(output, GeneratedOutput)
        self.assertEqual(output.metadata["backend"], "swiftui")
        self.assertEqual(output.metadata["screen_count"], 1)

        swift_files = [f for f in output.files if f.path.endswith(".swift")]
        self.assertEqual(len(swift_files), 1)
        self.assertEqual(swift_files[0].path, "LandingView.swift")
        self.assertEqual(swift_files[0].language, "swift")

        screen_ids = {n.node_id for n in self.plan.screens[0].walk() if n.node_id}
        self.assertTrue(screen_ids)
        self.assertEqual(set(swift_files[0].node_ids), screen_ids)

    def test_no_placeholder_markers(self):
        self.assertNotIn("TODO: Generate from LayoutPlan", self._view().content)

    def test_structural_markers(self):
        content = self._view().content
        self.assertIn("struct LandingView: View {", content)
        self.assertIn("var body: some View {", content)
        self.assertIn("#Preview {", content)
        self.assertIn("import SwiftUI", content)

    def test_container_lowering(self):
        content = self._view().content
        # Flex column -> VStack with alignment + spacing.
        self.assertIn("VStack(alignment: .trailing, spacing: 24)", content)
        # Flex row -> HStack.
        self.assertIn("HStack", content)

    def test_style_modifiers(self):
        content = self._view().content
        self.assertIn(".frame(width: 120, height: 48)", content)
        # Uniform padding -> single .padding(N).
        self.assertIn(".padding(24)", content)
        # Button fill -> background on the container.
        self.assertIn(".background(Color(red: 0.20, green: 0.40, blue: 0.80))", content)
        self.assertIn(".cornerRadius(8)", content)
        self.assertIn(".opacity(0.9)", content)
        # Title text color -> foregroundColor.
        self.assertIn(".foregroundColor(Color(red: 0.08, green: 0.12, blue: 0.24))", content)

    # -- real main-axis justification: Spacer() in VStack/HStack --
    def _justify_content(self, justify: str) -> str:
        output = self.backend.generate(
            document=_justify_document(), layout_plan=_justify_plan(justify),
        )
        return [f for f in output.files if f.path.endswith(".swift")][0].content

    def test_justify_center_leading_and_trailing_spacers(self):
        content = self._justify_content("CENTER")
        self.assertEqual(content.count("Spacer()"), 2)

    def test_justify_max_leading_spacer(self):
        content = self._justify_content("MAX")
        self.assertEqual(content.count("Spacer()"), 1)

    def test_justify_space_between_spacers_between_children(self):
        content = self._justify_content("SPACE_BETWEEN")
        # Three children -> a Spacer between each pair.
        self.assertEqual(content.count("Spacer()"), 2)

    def test_justify_min_no_spacers(self):
        content = self._justify_content("MIN")
        self.assertNotIn("Spacer()", content)

    def test_justify_lifted_to_supported(self):
        caps = self.backend.capabilities
        self.assertIn("justify", caps.supported_features)
        self.assertNotIn("justify", caps.partial_features)

    def test_typography_modifiers(self):
        content = self._view().content
        self.assertIn('.font(.custom("Inter", size: 32, weight: .bold))', content)
        self.assertIn(".multilineTextAlignment(.center)", content)
        self.assertIn('.font(.custom("Inter", size: 16, weight: .semibold))', content)
        self.assertIn(".kerning(0.5)", content)
        self.assertIn(".lineSpacing(24)", content)

    # -- declared-supported features must be emitted, never silently dropped --
    def test_supported_features_not_silently_dropped(self):
        doc, plan = _rich_fixture()
        view = [f for f in self.backend.generate(
            document=doc, layout_plan=plan,
        ).files if f.path.endswith(".swift")][0]
        content = view.content
        # Shadows -> .shadow with color opacity.
        self.assertIn(
            ".shadow(color: Color(red: 0.00, green: 0.00, blue: 0.00)"
            ".opacity(0.25), radius: 8, x: 0, y: 4)",
            content,
        )
        # Text decoration -> .underline().
        self.assertIn(".underline()", content)
        # Overflow clip -> .clipped().
        self.assertIn(".clipped()", content)
        # Fill sizing -> .frame(maxWidth: .infinity).
        self.assertIn(".frame(maxWidth: .infinity)", content)
        # Font family -> .custom.
        self.assertIn('.font(.custom("Inter", size: 14, weight: .semibold))', content)

    def test_unsupported_features_losses(self):
        doc, plan = _unsupported_fixture()
        output = self.backend.generate(document=doc, layout_plan=plan)
        losses = [l for l in output.fidelity_losses if l.node_id == "img:1"]
        self.assertTrue(losses, "image fills should be a preflight loss")
        self.assertEqual(losses[0].feature, "fills_image")

        view = [f for f in output.files if f.path.endswith(".swift")][0]
        content = view.content
        # Image fill degrades with a // fidelity: marker.
        self.assertIn("// fidelity: fills_image", content)
        # Gradient is genuinely representable (partial, real LinearGradient).
        self.assertIn("LinearGradient", content)
        # Absolute is genuinely supported (.position).
        self.assertIn(".position(x: 8, y: 8)", content)
        self.assertNotIn("fidelity: absolute_positioning", content)

    def test_deterministic(self):
        a = self._view().content
        b = self._view().content
        self.assertEqual(a, b)

    def test_golden_snapshot(self):
        snapshot_path = SNAPSHOT_DIR / SNAPSHOT_NAME

        if os.environ.get(REWRITE_ENV) == "1":
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(self._view().content, encoding="utf-8")
            self.skipTest(f"Snapshot regenerated at {snapshot_path}")

        if not snapshot_path.exists():
            self.fail(
                f"Snapshot {snapshot_path} is missing. "
                f"Run with {REWRITE_ENV}=1 to generate it."
            )
        self.assertEqual(
            self._view().content,
            snapshot_path.read_text(encoding="utf-8"),
            "Generated view no longer matches the snapshot. If the change is "
            f"intended, regenerate with {REWRITE_ENV}=1 and review the diff.",
        )

    def test_capabilities_unchanged(self):
        caps = self.backend.capabilities
        self.assertEqual(caps.styling_system, "swiftui_modifiers")
        self.assertEqual(caps.framework, "swiftui")
        self.assertEqual(caps.renderer, "xcode_preview")
        self.assertEqual(caps.file_extensions, (".swift",))
        self.assertIn("flex", caps.supported_features)
        self.assertIn("fills_image", caps.unsupported_features)
        # FILL_SIZE is genuinely supported — never in both buckets.
        self.assertNotIn("fills_image", caps.supported_features)
        # Declared partial, never supported: assets, instances, tokens.
        for feature in ("image_assets", "component_instances", "design_tokens"):
            self.assertIn(feature, caps.partial_features)
            self.assertNotIn(feature, caps.supported_features)


if __name__ == "__main__":
    unittest.main(verbosity=2)
