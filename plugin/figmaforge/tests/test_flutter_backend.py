#!/usr/bin/env python3
"""
Flutter backend tests (Part 14 Task 6).

Exercises the real .dart widget generator: file set + node coverage, no
placeholder markers, structural markers (class …Screen extends
StatelessWidget / Scaffold / build(BuildContext context)), Row/Column
container lowering with mainAxisAlignment/crossAxisAlignment and SizedBox
gap separators, Container + BoxDecoration + EdgeInsets style widgets,
Text + TextStyle typography, Align wrappers, fidelity-loss degradation with
// fidelity: markers, determinism, a golden snapshot, and unchanged
capabilities.

Run:  python3 -m unittest tests.test_flutter_backend -v
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
from backends.flutter import FlutterBackend
from core.ir_types import (
    IResponsive,
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
    SIZING_FIXED,
    SpacingSpec,
    SizingSpec,
    TextModel,
)

SNAPSHOT_DIR = plugin_root / "tests" / "snapshots" / "backends"
SNAPSHOT_NAME = "landing_screen.dart"
REWRITE_ENV = "REWRITE_SNAPSHOTS"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_document() -> IRDocument:
    title = IRNode(
        id="t:1", name="Title", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="dart", node_id="t:1"),
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
        source=IRSource(file_key="dart", node_id="t:2"),
        typography=IRTypography(
            font_family="Inter", font_size=16.0, font_weight=600.0,
            line_height=24.0,
        ),
        text=IRTextContent(characters="Click me"),
    )
    button = IRNode(
        id="btn:1", name="Button", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="dart", node_id="btn:1"),
        style=IRStyle(
            fills=[IRFill(kind="solid", color=IRColor(r=0.2, g=0.4, b=0.8, a=1.0))],
            radius=8.0,
        ),
        children=[btn_label],
    )
    root = IRNode(
        id="0:1", name="Landing", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="dart", node_id="0:1"),
        children=[title, button],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="dart", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="dart", name="DartLanding", pages=[page])
    doc.root = root
    return doc


def _dart_plan() -> LayoutPlan:
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
        alignment=AlignmentSpec(align="CENTER"),
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
    return LayoutPlan(file_key="dart", viewport=390.0, screens=[screen])


def _rich_fixture():
    """Shadows, decoration, letter spacing, text case, and overflow clip."""
    label = IRNode(
        id="t:3", name="Label", kind=KIND_TEXT, node_type="TEXT",
        source=IRSource(file_key="dart", node_id="t:3"),
        typography=IRTypography(
            font_size=14.0, font_weight=600.0, letter_spacing=0.5,
            text_decoration="UNDERLINE", text_case="UPPER",
        ),
        text=IRTextContent(characters="Save changes"),
    )
    card = IRNode(
        id="card:1", name="Card", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="dart", node_id="card:1"),
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
        source=IRSource(file_key="dart", node_id="page-1"),
        children=[card],
    )
    doc = IRDocument(file_key="dart", name="Rich", pages=[page])
    doc.root = card

    card_plan = LayoutNodePlan(
        node_id="card:1", name="Card", kind="frame", display=DISPLAY_FLEX,
        box=Box(x=0, y=0, width=200, height=80),
        overflow=OverflowSpec(x=OVERFLOW_CLIP, y=OVERFLOW_CLIP),
    )
    label_plan = LayoutNodePlan(
        node_id="t:3", name="Label", kind="text", display=DISPLAY_NONE,
        text=TextModel(characters="Save changes"),
    )
    card_plan.children.append(label_plan)
    plan = LayoutPlan(file_key="dart", viewport=390.0, screens=[card_plan])
    return doc, plan


def _unsupported_fixture():
    """Gradient + absolute + responsive nodes (media queries is the dart loss)."""
    grad_node = IRNode(
        id="grad:1", name="Gradient", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="dart", node_id="grad:1"),
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
        source=IRSource(file_key="dart", node_id="abs:1"),
        position=IRPosition(mode="absolute", left=8.0, top=8.0),
        style=IRStyle(fills=[IRFill(
            kind="solid", color=IRColor(r=1.0, g=0.0, b=0.0, a=1.0),
        )]),
    )
    resp_node = IRNode(
        id="resp:1", name="Banner", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="dart", node_id="resp:1"),
        responsive=IResponsive(constraints_horizontal="STRETCH"),
        style=IRStyle(fills=[IRFill(
            kind="solid", color=IRColor(r=0.0, g=1.0, b=0.0, a=1.0),
        )]),
    )
    root = IRNode(
        id="0:9", name="Overlay", kind=KIND_FRAME, node_type="FRAME",
        source=IRSource(file_key="dart", node_id="0:9"),
        children=[grad_node, abs_node, resp_node],
    )
    page = IRNode(
        id="page-1", name="Page", kind=KIND_PAGE, node_type="CANVAS",
        source=IRSource(file_key="dart", node_id="page-1"),
        children=[root],
    )
    doc = IRDocument(file_key="dart", name="Overlay", pages=[page])
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
        node_id="resp:1", name="Banner", kind="frame", display=DISPLAY_NONE,
        box=Box(x=0, y=0, width=100, height=50),
    ))
    plan = LayoutPlan(file_key="dart", viewport=390.0, screens=[screen])
    return doc, plan


class TestFlutterBackend(unittest.TestCase):
    def setUp(self):
        self.backend = FlutterBackend()
        self.doc = _make_document()
        self.plan = _dart_plan()

    def _generate(self):
        return self.backend.generate(document=self.doc, layout_plan=self.plan)

    def _widget(self):
        return [f for f in self._generate().files if f.path.endswith(".dart")][0]

    def test_generate_files_and_node_coverage(self):
        output = self._generate()
        self.assertIsInstance(output, GeneratedOutput)
        self.assertEqual(output.metadata["backend"], "flutter")
        self.assertEqual(output.metadata["screen_count"], 1)

        dart_files = [f for f in output.files if f.path.endswith(".dart")]
        self.assertEqual(len(dart_files), 1)
        self.assertEqual(dart_files[0].path, "landing_screen.dart")
        self.assertEqual(dart_files[0].language, "dart")

        screen_ids = {n.node_id for n in self.plan.screens[0].walk() if n.node_id}
        self.assertTrue(screen_ids)
        self.assertEqual(set(dart_files[0].node_ids), screen_ids)

    def test_no_placeholder_markers(self):
        self.assertNotIn("TODO: Generate from LayoutPlan", self._widget().content)

    def test_structural_markers(self):
        content = self._widget().content
        self.assertIn("class LandingScreen extends StatelessWidget {", content)
        self.assertIn("Scaffold", content)
        self.assertIn("Widget build(BuildContext context) {", content)
        self.assertIn("import 'package:flutter/material.dart';", content)

    def test_container_lowering(self):
        content = self._widget().content
        # Flex column -> Column with axis alignments + gap separators.
        self.assertIn("Column(", content)
        self.assertIn("mainAxisAlignment: MainAxisAlignment.center", content)
        self.assertIn("crossAxisAlignment: CrossAxisAlignment.end", content)
        self.assertIn("SizedBox(height: 24)", content)
        # Flex row -> Row (button).
        self.assertIn("Row(", content)

    def test_style_widgets(self):
        content = self._widget().content
        # Button: Container with BoxDecoration (color + radius) + fixed size.
        self.assertIn("Container(", content)
        self.assertIn("decoration: BoxDecoration(", content)
        self.assertIn("color: Color(0xFF3366CC)", content)
        self.assertIn("borderRadius: BorderRadius.circular(8)", content)
        self.assertIn("width: 120,", content)
        self.assertIn("height: 48,", content)
        # Root padding.
        self.assertIn("padding: EdgeInsets.all(24)", content)

    def test_typography(self):
        content = self._widget().content
        self.assertIn("TextStyle(", content)
        self.assertIn("fontSize: 32,", content)
        self.assertIn("fontWeight: FontWeight.w700", content)
        self.assertIn("color: Color(0xFF141F3D)", content)
        self.assertIn("textAlign: TextAlign.center", content)
        # Line-height multiplier.
        self.assertIn("height: 1.5", content)
        # Alignment -> Align wrapper.
        self.assertIn("Align(", content)
        self.assertIn("alignment: Alignment.center", content)

    # -- declared-supported features must be emitted, never silently dropped --
    def test_supported_features_not_silently_dropped(self):
        doc, plan = _rich_fixture()
        widget = [f for f in self.backend.generate(
            document=doc, layout_plan=plan,
        ).files if f.path.endswith(".dart")][0]
        content = widget.content
        # Shadows -> BoxShadow with alpha hex.
        self.assertIn("boxShadow: [BoxShadow(", content)
        self.assertIn("color: Color(0x40000000)", content)
        self.assertIn("blurRadius: 8,", content)
        self.assertIn("offset: Offset(0, 4),", content)
        # Text decoration + letter spacing.
        self.assertIn("decoration: TextDecoration.underline", content)
        self.assertIn("letterSpacing: 0.5,", content)
        # Text case -> literal transform.
        self.assertIn("'SAVE CHANGES'", content)
        # Overflow clip -> clipBehavior.
        self.assertIn("clipBehavior: Clip.hardEdge,", content)

    def test_unsupported_features_losses(self):
        doc, plan = _unsupported_fixture()
        output = self.backend.generate(document=doc, layout_plan=plan)
        losses = [l for l in output.fidelity_losses if l.node_id == "resp:1"]
        self.assertTrue(losses, "media queries should be a preflight loss")
        self.assertEqual(losses[0].feature, "media_queries")

        widget = [f for f in output.files if f.path.endswith(".dart")][0]
        content = widget.content
        # Responsive node degrades with a // fidelity: marker.
        self.assertIn("// fidelity: media_queries", content)
        # Gradient is genuinely representable (LinearGradient).
        self.assertIn("LinearGradient", content)
        # Absolute positioning is genuinely supported (Stack + Positioned).
        self.assertIn("Positioned(", content)
        self.assertIn("left: 8, top: 8,", content)
        self.assertNotIn("fidelity: absolute_positioning", content)

    def test_deterministic(self):
        a = self._widget().content
        b = self._widget().content
        self.assertEqual(a, b)

    def test_golden_snapshot(self):
        snapshot_path = SNAPSHOT_DIR / SNAPSHOT_NAME

        if os.environ.get(REWRITE_ENV) == "1":
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(self._widget().content, encoding="utf-8")
            self.skipTest(f"Snapshot regenerated at {snapshot_path}")

        if not snapshot_path.exists():
            self.fail(
                f"Snapshot {snapshot_path} is missing. "
                f"Run with {REWRITE_ENV}=1 to generate it."
            )
        self.assertEqual(
            self._widget().content,
            snapshot_path.read_text(encoding="utf-8"),
            "Generated widget no longer matches the snapshot. If the change is "
            f"intended, regenerate with {REWRITE_ENV}=1 and review the diff.",
        )

    def test_capabilities_unchanged(self):
        caps = self.backend.capabilities
        self.assertEqual(caps.styling_system, "flutter_widgets")
        self.assertEqual(caps.framework, "flutter")
        self.assertEqual(caps.renderer, "flutter_simulator")
        self.assertEqual(caps.file_extensions, (".dart",))
        self.assertIn("flex", caps.supported_features)
        self.assertIn("media_queries", caps.unsupported_features)
        self.assertIn("fills_image", caps.partial_features)
        # Declared partial, never supported: scroll, assets, instances, tokens.
        for feature in ("overflow_scroll", "image_assets",
                        "component_instances", "design_tokens"):
            self.assertIn(feature, caps.partial_features)
            self.assertNotIn(feature, caps.supported_features)


if __name__ == "__main__":
    unittest.main(verbosity=2)
